"""FR-030 / FR-031 / FR-057: the computed confidence, over its whole domain.

T056, and it is the **red** half of a strict red-green pair (`plan.md` §The
test-first boundary). This file was authored and run against an absent
`model.compute.confidence` and observed to fail with a collection error before a
line of that module existed; T057 is what makes it pass. A test task marked
complete beside a green suite is the defect the ordering condition exists to
name, so the observed failure is recorded on T056's task line.

**The relation class is an alternate implementation over an exhaustively
enumerated domain** (`plan.md`). FR-057's three binary signals admit exactly
eight combinations, so the domain is *covered* rather than sampled — a property
drawing from an eight-point domain is strictly weaker than visiting all eight.
Each of the eight is checked against an independently written expression of the
same policy, and the comparison is **bit equality**, because SC-026 requires a
recomputation to reproduce the stored value exactly and `double precision`
subtraction is not associative.

Hypothesis is used here for the **weights and the floor**, which are inputs read
off the run row (`ingestion_run.confidence_floor` and the three deduction
columns), never for the signals. That split is the point: the signals are a
finite closed domain and the policy is not.

**No weight and no floor is written as a literal in this file.** FR-032 and
FR-057 put the declared numbers on the run row so a stored score is checkable
against the policy that produced it; a test asserting `0.15` would pass against
a run scored under different weights, which is precisely the defect the columns
exist to close. What is asserted here is the *shape* of the policy — the
application order, the arithmetic, and the two exclusions FR-057 names — over
every weight-and-floor triple the run row's own `CHECK`s admit.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from model.compute.confidence import (
    DEDUCTION_ORDER,
    LABEL_MATCHES,
    SIGNAL_DOMAIN,
    ConfidenceError,
    DeductionWeights,
    ParseSignals,
    compute_confidence,
)

#: Weight components. Bounded to the range `ck_ingestion_run__deduction_*_range`
#: admits, and excluded from NaN and infinity, which that column cannot hold
#: either — `double precision` accepts them but the range check rejects both.
weight_components = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@st.composite
def admissible_weights(draw: st.DrawFn) -> DeductionWeights:
    """Weight triples the run row admits *and* this module accepts.

    The extra condition beyond the row's own checks is that the worst
    combination stays at or above zero, which is what keeps every score inside
    `ck_extracted_value__confidence_range`.

    Drawn as a **budget consumed left to right** rather than generated freely
    and filtered: each weight is bounded by what the previous two left, using
    the same `((1.0 - a) - p) - r` arithmetic the computation uses, so the
    strategy cannot produce a triple the constructor then refuses and no example
    is discarded. A `filter` here would reject the large majority of triples —
    three independent draws from [0, 1] rarely sum below one — and Hypothesis
    would report the health-check failure rather than the property.
    """
    alternate = draw(weight_components)
    after_alternate = 1.0 - alternate
    page = draw(
        st.floats(min_value=0.0, max_value=after_alternate, allow_nan=False, allow_infinity=False)
    )
    after_page = after_alternate - page
    repaired = draw(
        st.floats(min_value=0.0, max_value=after_page, allow_nan=False, allow_infinity=False)
    )
    return DeductionWeights(alternate_label=alternate, page_split=page, repaired=repaired)


def worst_score(weights: DeductionWeights) -> float:
    """`1.0` less all three deductions, in the declared order."""
    return ((1.0 - weights.alternate_label) - weights.page_split) - weights.repaired


def policy_admits(weights: DeductionWeights, floor: float) -> bool:
    """`ck_ingestion_run__floor_excludes_repair` and `..._alt_split`, as written.

    Transcribed from the two `CHECK` bodies rather than paraphrased: they are
    the definition of a legal policy, and a test generating triples outside them
    would be asserting over runs the database refuses to record.
    """
    return floor > 1.0 - weights.repaired and floor > (1.0 - weights.alternate_label) - (
        weights.page_split
    )


# ---------------------------------------------------------------------------
# The domain: eight combinations, enumerated rather than sampled
# ---------------------------------------------------------------------------


def test_the_domain_is_exactly_the_eight_combinations_the_signals_admit() -> None:
    """FR-057's three binary signals, so eight and not one fewer.

    A missing member would silently narrow every property below, and FR-033
    publishes a distribution over all eight with a score nothing took appearing
    as a zero — so an incomplete domain is also a report with a row missing.
    """
    assert len(SIGNAL_DOMAIN) == 8
    keys = {
        (entry.alternate_label, entry.page_split, entry.validated_after_repair)
        for entry in SIGNAL_DOMAIN
    }
    assert len(keys) == 8
    assert keys == {
        (alternate, split, repaired)
        for alternate in (False, True)
        for split in (False, True)
        for repaired in (False, True)
    }


def test_the_label_vocabulary_is_the_signal_columns_own_two_values() -> None:
    """`ck_extracted_value_parse_signal__label_match` admits exactly these."""
    assert set(LABEL_MATCHES) == {"canonical", "alternate"}
    assert {entry.label_match for entry in SIGNAL_DOMAIN} == set(LABEL_MATCHES)


def test_the_page_split_signal_is_the_values_own_source_chunk_count() -> None:
    """Not an independent boolean (`data-model.md` §extracted_value_parse_signal).

    A `page_split` column here would be a second answer that can disagree with
    the value's own provenance, and the disagreement would be invisible: the
    recomputation would read the copy while the citation read the original.
    """
    assert all(entry.source_chunk_count >= 1 for entry in SIGNAL_DOMAIN)
    assert ParseSignals("canonical", 1, False).page_split is False
    assert ParseSignals("canonical", 2, False).page_split is True
    assert ParseSignals("canonical", 7, False).page_split is True


# ---------------------------------------------------------------------------
# The alternate implementation, over the whole domain, at bit equality
# ---------------------------------------------------------------------------


def independently_computed(signals: ParseSignals, weights: DeductionWeights) -> float:
    """FR-057 written out again, from the requirement rather than from T057.

    Deliberately a different shape from the implementation — an explicit
    accumulator with three guarded statements — so that a defect in one is not
    reproduced by the other. What it may not differ in is the *order*: the
    requirement fixes `((1.0 - alternate) - page_split) - repaired`, and a
    reordering here would make this an alternate policy rather than an alternate
    implementation.
    """
    score = 1.0
    if signals.label_match == "alternate":
        score = score - weights.alternate_label
    if signals.source_chunk_count > 1:
        score = score - weights.page_split
    if signals.validated_after_repair:
        score = score - weights.repaired
    return score


@given(weights=admissible_weights())
def test_every_one_of_the_eight_matches_an_independent_expression(
    weights: DeductionWeights,
) -> None:
    """SC-026's "exactly", as bit equality and not equality within a tolerance."""
    for signals in SIGNAL_DOMAIN:
        expected = independently_computed(signals, weights)
        observed = compute_confidence(signals, weights)
        assert observed == expected
        assert observed.hex() == expected.hex()


@given(weights=admissible_weights())
def test_the_deductions_are_applied_left_to_right_and_not_summed_first(
    weights: DeductionWeights,
) -> None:
    """`data-model.md` §ingestion_run: the order is part of the record.

    `1.0 - 0.15 - 0.10` and `1.0 - (0.15 + 0.10)` need not be bit-identical, so
    declaring the order is what lets "reproduces the stored value exactly" mean
    something. Asserted as agreement with the declared grouping rather than as
    disagreement with the other one, which would only hold for some weights.
    """
    everything = ParseSignals("alternate", 2, True)
    assert compute_confidence(everything, weights) == worst_score(weights)


def test_the_declared_order_is_alternate_then_page_split_then_repaired() -> None:
    """The order is published (FR-046), so it is named rather than only obeyed."""
    assert DEDUCTION_ORDER == ("alternate_label", "page_split", "repaired")


def test_a_grouped_sum_is_a_different_number_and_this_would_notice() -> None:
    """The guard on the test above: a comparison nothing can distinguish is not
    a test. At these weights the two groupings differ in the last bit, so an
    implementation that summed first would fail the property rather than passing
    it for the same reason a correct one does.

    The three values are chosen for their floating-point behaviour and are not
    the declared policy — the declared weights live on the run row, and this
    file states none of them.
    """
    weights = DeductionWeights(alternate_label=0.1, page_split=0.2, repaired=0.3)
    left_to_right = ((1.0 - 0.1) - 0.2) - 0.3
    summed_first = 1.0 - (0.1 + 0.2 + 0.3)
    assert left_to_right != summed_first
    assert compute_confidence(ParseSignals("alternate", 2, True), weights) == left_to_right


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


@given(weights=admissible_weights())
def test_no_signal_firing_is_exactly_one(weights: DeductionWeights) -> None:
    """An absent term is skipped, not subtracted as a zero it may not equal."""
    assert compute_confidence(ParseSignals("canonical", 1, False), weights) == 1.0


@given(weights=admissible_weights())
def test_every_score_lies_inside_the_stored_columns_range(
    weights: DeductionWeights,
) -> None:
    """`ck_extracted_value__confidence_range` admits [0.0, 1.0] at both ends."""
    for signals in SIGNAL_DOMAIN:
        assert 0.0 <= compute_confidence(signals, weights) <= 1.0


@given(weights=admissible_weights())
def test_the_score_is_non_increasing_as_a_deduction_is_added(
    weights: DeductionWeights,
) -> None:
    """Adding a signal may never raise a score. A deduction that improved the
    number would invert the ordering the score exists to provide."""
    for signals in SIGNAL_DOMAIN:
        base = compute_confidence(signals, weights)
        if not signals.alternate_label:
            worse = ParseSignals(
                "alternate", signals.source_chunk_count, signals.validated_after_repair
            )
            assert compute_confidence(worse, weights) <= base
        if not signals.page_split:
            worse = ParseSignals(signals.label_match, 2, signals.validated_after_repair)
            assert compute_confidence(worse, weights) <= base
        if not signals.validated_after_repair:
            worse = ParseSignals(signals.label_match, signals.source_chunk_count, True)
            assert compute_confidence(worse, weights) <= base


@given(weights=admissible_weights(), count=st.integers(min_value=2, max_value=40))
def test_the_page_split_deduction_is_taken_once_however_many_chunks(
    weights: DeductionWeights, count: int
) -> None:
    """FR-057 deducts for "assembled across a page break", not per chunk.

    Scaling by the count is arithmetic the requirement does not state, and it
    would make two values of equal provenance quality score differently for the
    length of the text between them.
    """
    two = compute_confidence(ParseSignals("canonical", 2, False), weights)
    many = compute_confidence(ParseSignals("canonical", count, False), weights)
    assert two == many


@given(weights=admissible_weights(), attempts=st.integers(min_value=1, max_value=5))
def test_the_repair_deduction_is_taken_once_however_many_attempts(
    weights: DeductionWeights, attempts: int
) -> None:
    """`validated_after_repair` is a boolean and not a count, for this reason.

    A count here would invite the deduction to be scaled by it, which FR-057
    does not state and which the spec's own assumption forecloses by fixing the
    budget at one attempt.
    """
    del attempts  # the signal admits no attempt count to scale by
    assert compute_confidence(ParseSignals("canonical", 1, True), weights) == 1.0 - (
        weights.repaired
    )


# ---------------------------------------------------------------------------
# FR-057's two named exclusions, over every policy the run row admits
# ---------------------------------------------------------------------------


@given(data=st.data())
def test_the_five_excluded_combinations_are_below_any_admissible_floor(
    data: st.DataObject,
) -> None:
    """FR-057 states the floor by what it rejects: any repaired invocation, and
    any value both alternate-labelled and page-split.

    Asserted over every `(weights, floor)` triple the run row's two exclusion
    `CHECK`s admit, rather than at one declared policy — the declared numbers
    live on the row, and a test naming them would pass against a run scored
    under different ones.
    """
    weights = data.draw(admissible_weights())
    lower = max(1.0 - weights.repaired, (1.0 - weights.alternate_label) - weights.page_split)
    assume(lower < 1.0)
    floor = data.draw(
        st.floats(
            min_value=lower,
            max_value=1.0,
            exclude_min=True,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    assume(policy_admits(weights, floor))

    excluded = [
        signals
        for signals in SIGNAL_DOMAIN
        if signals.validated_after_repair or (signals.alternate_label and signals.page_split)
    ]
    assert len(excluded) == 5
    for signals in excluded:
        assert compute_confidence(signals, weights) < floor


def test_exactly_three_combinations_are_not_named_by_either_exclusion() -> None:
    """AD-008: three survive, and which three is a fact about the signals alone.

    Stated without a weight or a floor, because it is the *shape* of the two
    exclusions rather than a consequence of any particular policy.
    """
    survivors = {
        (entry.label_match, entry.source_chunk_count > 1, entry.validated_after_repair)
        for entry in SIGNAL_DOMAIN
        if not entry.validated_after_repair and not (entry.alternate_label and entry.page_split)
    }
    assert survivors == {
        ("canonical", False, False),
        ("canonical", True, False),
        ("alternate", False, False),
    }


# ---------------------------------------------------------------------------
# Refusals — nothing is defaulted, coerced, or clamped
# ---------------------------------------------------------------------------


def test_a_label_match_outside_the_two_values_is_refused() -> None:
    """The column's vocabulary is closed; a third value is a migration."""
    with pytest.raises(ConfidenceError, match="label_match"):
        ParseSignals("Canonical", 1, False)


def test_a_source_chunk_count_below_one_is_refused() -> None:
    """`ck_extracted_value_parse_signal__source_count_positive`. A stored value
    is assembled from at least one chunk, and zero would make the page-split
    signal read `False` for a value with no provenance at all."""
    with pytest.raises(ConfidenceError, match="source_chunk_count"):
        ParseSignals("canonical", 0, False)


@given(weight=st.floats(allow_nan=False, allow_infinity=False).filter(lambda v: v < 0.0 or v > 1.0))
def test_a_weight_outside_the_columns_range_is_refused(weight: float) -> None:
    """`ck_ingestion_run__deduction_*_range` admits [0.0, 1.0]; so does this."""
    with pytest.raises(ConfidenceError):
        DeductionWeights(alternate_label=weight, page_split=0.0, repaired=0.0)


def test_weights_that_could_drive_a_score_below_zero_are_refused() -> None:
    """Refused at declaration rather than at the write.

    The database cannot state this one: it is a cross-column rule on
    `ingestion_run` that revision `0400` does not carry, and `0400` is applied
    and forward-only. A policy whose worst combination scores below zero has a
    signal combination `ck_extracted_value__confidence_range` cannot hold, so it
    is refused where it is declared rather than producing an unstorable number
    at the end of a run.
    """
    with pytest.raises(ConfidenceError, match="below zero"):
        DeductionWeights(alternate_label=0.5, page_split=0.4, repaired=0.3)


def test_a_nan_weight_is_refused() -> None:
    """A NaN compares false against every bound, so a range check written the
    other way round would admit it silently."""
    with pytest.raises(ConfidenceError):
        DeductionWeights(alternate_label=float("nan"), page_split=0.0, repaired=0.0)
