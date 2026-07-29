"""Properties of the displayed probability.

FR-039's second half. Written before `api.compute.probability` existed and
watched failing.

Three rules interact here and each exists to close a specific way a figure lies:

- **Whole percent** (FR-008) — several thousand draws support one percent and no
  more; a finer figure asserts resolution the artifact does not have.
- **Bounded forms** — `<1%` and `>99%` rather than `0%` and `100%`, *including*
  for a stored probability of exactly zero or one. Several thousand draws cannot
  evidence a certainty, and an exact zero in the array is itself an estimate at
  the resolution the draw count supports.
- **The complement is subtracted, not re-rounded** — so a mandated FR-006 pair
  always sums to one hundred. Rounding both directions independently produces
  pairs summing to 99 or 101, which reads as an arithmetic bug and undermines
  the pair the dual framing exists to provide.

The third rule holds *only* where both directions render as integers. At a
bounded form there is no integer to subtract from, so the pair does not sum to
one hundred and asserting that it does would be asserting something false.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from api.compute.probability import PercentFigure, complement, percent_figure

#: Stored probabilities: the domain `ck_line_posterior__survival_unit_interval`
#: admits. Both endpoints included, because both are reachable and both take the
#: bounded form.
stored_probability = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (0.125, "13%"),
        (0.875, "88%"),
        (0.005, "1%"),
        (0.015, "2%"),
        (0.345, "35%"),
    ],
)
def test_rounding_is_half_up_at_the_percent_scale(stored: float, expected: str) -> None:
    """FR-008 states the rule and its worked example: a stored `0.125` renders
    `13%` and not the `12%` a half-to-even rule produces.

    Named explicitly because Python's own `round` is half-to-even, so the
    default behaviour is the wrong one and would pass unnoticed at every value
    that is not exactly on a half.
    """
    assert percent_figure(stored).display == expected


@given(stored=stored_probability)
def test_a_figure_is_whole_percent_or_a_bound_and_never_anything_else(stored: float) -> None:
    """FR-008. There is no third form — no decimals, no `0%`, no `100%`."""
    figure = percent_figure(stored)
    if figure.bounded:
        assert figure.display in {"<1%", ">99%"}
    else:
        assert figure.display.endswith("%")
        assert figure.percent is not None
        assert 1 <= figure.percent <= 99
        assert figure.display == f"{figure.percent}%"


@pytest.mark.parametrize("stored", [0.0, 1.0, 0.0004, 0.9996])
def test_the_extremes_take_the_bounded_form_including_exact_zero_and_one(stored: float) -> None:
    """FR-008. "There is no endpoint at which the bounded form is skipped."

    A stored exact zero is the case a reader expects to be exempt, and it is
    the one that most needs the rule: `0%` on a screen is a promise, and the
    posterior is not in a position to make one.
    """
    figure = percent_figure(stored)
    assert figure.bounded
    assert figure.percent is None
    assert figure.display in {"<1%", ">99%"}


@given(stored=stored_probability)
def test_a_pair_of_unbounded_point_figures_sums_to_one_hundred(stored: float) -> None:
    """FR-008, FR-006. The complement is one hundred minus the *displayed*
    integer, never a second independent rounding.

    Scoped to unbounded point figures on purpose. At a bounded form there is no
    integer to subtract from — `<1%` pairs with `>99%` — so the sum is not
    defined there and claiming it is would be asserting a falsehood about the
    very forms the bound exists to introduce.
    """
    figure = percent_figure(stored)
    other = complement(figure)

    if figure.bounded:
        assert other.bounded, (
            "a bounded value paired with a flat certainty reintroduces through the "
            "complement exactly what the bound removes"
        )
        return

    assert not other.bounded
    assert figure.percent is not None and other.percent is not None
    assert figure.percent + other.percent == 100


@given(stored=stored_probability)
def test_the_complement_of_the_complement_is_the_original(stored: float) -> None:
    """A dual framing a coordinator can read in either direction has to agree
    with itself; otherwise which of the two is 'the' figure starts to matter."""
    figure = percent_figure(stored)
    assert complement(complement(figure)) == figure


@given(
    first=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    second=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_a_larger_stored_probability_never_displays_as_a_smaller_one(
    first: float, second: float
) -> None:
    """FR-013's comparison rule, as a property of the display itself.

    "`<1%` ranks below every integer and `>99%` above every integer" is what
    keeps FR-013 decidable at the bounded forms rather than undefined there, so
    the ordering over displayed forms is asserted here where it is defined.
    """
    if first > second:
        first, second = second, first
    assert _rank(percent_figure(first)) <= _rank(percent_figure(second))


def _rank(figure: PercentFigure) -> Decimal:
    """Order over displayed forms: `<1%` below every integer, `>99%` above."""
    if figure.bounded:
        return Decimal("0.5") if figure.display == "<1%" else Decimal("99.5")
    assert figure.percent is not None
    return Decimal(figure.percent)


def test_a_figure_states_its_own_reference_class() -> None:
    """FR-003, FR-005. A percentage with no stated reference class is read as a
    confidence rather than as a frequency, which is the failure the research
    named — so the class travels with the figure rather than being applied by
    whichever renderer happens to remember."""
    figure = percent_figure(0.35)
    assert figure.reference_class
    assert "100" in figure.reference_class or "hundred" in figure.reference_class


@pytest.mark.parametrize("stored", [-0.001, 1.001])
def test_a_probability_outside_the_unit_interval_is_refused(stored: float) -> None:
    """`ck_line_posterior__survival_unit_interval` makes this unreachable from
    storage, so reaching it means the caller computed it — and silently clamping
    would turn a computation defect into a plausible-looking figure."""
    with pytest.raises(ValueError, match="unit interval"):
        percent_figure(stored)
