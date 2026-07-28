"""FR-033 / FR-034 / FR-046 / FR-069: report items 6, 7 and 13, and the ledger.

T063, T064 and T065. These are the three published artifacts US3 owes:

* **item 6** — the declared floor, the distribution over all eight scores with
  stored and rejected counted apart, the three weights and the order they are
  applied in;
* **item 7** — the failure count broken down by each of the seven outcomes,
  zeros included, denominated per attempt;
* **item 13** — valid, repaired and failed counts as an invocation-level table
  and an attempt-level table, each figure naming the unit it counts.

**Nothing here states a weight or a floor of its own.** The policy is read from
a `ConfidencePolicy` the caller supplies, exactly as the report reads it from the
run's own `ingestion_run` row. The tests that matter most below are the ones
using a policy that is *not* the declared one, because they are what shows the
section prints what the run used rather than what the code was compiled with.
"""

from __future__ import annotations

import pytest

from model.compute.confidence import (
    DEDUCTION_ORDER,
    SIGNAL_DOMAIN,
    DeductionWeights,
    ParseSignals,
    compute_confidence,
)
from model.ingest.cli import count_attempts
from model.ingest.failures import FAILURE_OUTCOMES, outcome_counts
from model.ingest.report import (
    ATTEMPT_UNIT,
    INVOCATION_UNIT,
    AttemptLedger,
    ConfidenceDistribution,
    InvocationLedger,
    ReportError,
    attempt_ledger_section,
    confidence_section,
    failure_breakdown_section,
    tally_confidence,
)
from model.ingest.runs import DECLARED_POLICY, ConfidencePolicy

RUN_ID = "3f1c0de0-0000-4000-8000-000000000006"

#: A policy that is deliberately **not** the declared one, so a section printing
#: the declared constants instead of what it was handed fails visibly.
OTHER_POLICY = ConfidencePolicy(
    floor=0.65,
    weights=DeductionWeights(alternate_label=0.3, page_split=0.1, repaired=0.45),
)


def stored_and_rejected(
    policy: ConfidencePolicy,
) -> tuple[tuple[ParseSignals, ...], tuple[ParseSignals, ...]]:
    """One value per combination, split by whether the policy's floor admits it.

    Derived from the policy rather than listed, so the split follows the run's
    own floor — which is the property FR-033 is about.
    """
    stored = tuple(
        signals
        for signals in SIGNAL_DOMAIN
        if policy.admits(compute_confidence(signals, policy.weights))
    )
    rejected = tuple(signals for signals in SIGNAL_DOMAIN if signals not in stored)
    return stored, rejected


# ---------------------------------------------------------------------------
# T064 / item 6 — floor, eight scores, weights, order
# ---------------------------------------------------------------------------


def test_the_distribution_covers_all_eight_scores_with_zeros_included() -> None:
    """SC-017: zero admissible scores are omitted.

    Only one combination is observed here, so seven of the eight rows are zeros
    — which is exactly what makes the difference between an absent row and a
    zero row visible.
    """
    distribution = tally_confidence([ParseSignals("canonical", 1, False)], [])
    assert len(distribution.stored) == 8
    assert len(distribution.rejected) == 8
    assert distribution.stored_total == 1
    assert distribution.rejected_total == 0
    assert sum(1 for value in distribution.stored.values() if value == 0) == 7


def test_a_distribution_missing_a_combination_is_refused() -> None:
    """A row omitted and a row of zero read the same to a reader, and only one
    of them is a measurement."""
    complete = tally_confidence([], [])
    partial = dict(complete.stored)
    partial.pop(next(iter(partial)))
    with pytest.raises(ReportError, match="published as a zero"):
        ConfidenceDistribution(stored=partial, rejected=complete.rejected)


def test_a_negative_count_in_a_distribution_is_refused() -> None:
    complete = tally_confidence([], [])
    negative = dict(complete.stored)
    negative[next(iter(negative))] = -1
    with pytest.raises(ReportError, match="negative count"):
        ConfidenceDistribution(stored=negative, rejected=complete.rejected)


def test_the_section_publishes_the_floor_the_weights_and_the_order() -> None:
    """FR-046: a score is explainable and recomputable from the stored row, so
    the report has to print what "the stored row" means."""
    stored, rejected = stored_and_rejected(DECLARED_POLICY)
    section = confidence_section(
        run_id=RUN_ID,
        policy=DECLARED_POLICY,
        distribution=tally_confidence(stored, rejected),
    )
    rendered = section.render()
    assert repr(DECLARED_POLICY.floor) in rendered
    for name in DEDUCTION_ORDER:
        assert repr(getattr(DECLARED_POLICY.weights, name)) in rendered
        assert f"`ingestion_run.deduction_{name}`" in rendered
    # The application order is published as positions, not merely obeyed.
    for position in range(1, len(DEDUCTION_ORDER) + 1):
        assert f"application order position {position}" in rendered


def test_the_section_prints_the_policy_it_was_given_not_the_declared_one() -> None:
    """The defect this closes: a report showing today's floor beside last run's
    distribution publishes a floor that never rejected anything in it."""
    stored, rejected = stored_and_rejected(OTHER_POLICY)
    rendered = confidence_section(
        run_id=RUN_ID, policy=OTHER_POLICY, distribution=tally_confidence(stored, rejected)
    ).render()
    assert repr(OTHER_POLICY.floor) in rendered
    assert f"**Declared floor: {DECLARED_POLICY.floor!r}**" not in rendered


def test_the_two_populations_are_counted_separately() -> None:
    """FR-033: the rejected half is carried from the run's own tally.

    A rejected score has no row — the value was recorded as a failure with
    outcome `confidence_below_threshold` — so a distribution queried from
    `extracted_value` would be the stored half only and would report that the
    floor rejected nothing.
    """
    stored, rejected = stored_and_rejected(DECLARED_POLICY)
    assert stored and rejected, "the declared floor both stores and rejects"
    distribution = tally_confidence(stored, rejected)
    section = confidence_section(run_id=RUN_ID, policy=DECLARED_POLICY, distribution=distribution)
    labels = {figure.label for figure in section.figures}
    assert "Values stored with their confidence intact" in labels
    assert "Scores the floor rejected" in labels
    assert distribution.computed_total == len(SIGNAL_DOMAIN)


def test_no_mean_confidence_is_published() -> None:
    """Principle II and FR-033: a distribution, never a mean.

    Two runs with identical mean confidence can differ entirely in what the
    floor rejected, which is the whole shape the floor is defined against.
    """
    stored, rejected = stored_and_rejected(DECLARED_POLICY)
    rendered = (
        confidence_section(
            run_id=RUN_ID, policy=DECLARED_POLICY, distribution=tally_confidence(stored, rejected)
        )
        .render()
        .lower()
    )
    assert "mean confidence" not in rendered
    assert "average confidence" not in rendered


def test_the_heuristic_statement_carries_its_reversal_trigger() -> None:
    """FR-033: printed beside the distribution and not only in a limitations
    table, and with the condition that would reverse it — a claim with no stated
    way of being wrong is not a disclosure."""
    stored, rejected = stored_and_rejected(DECLARED_POLICY)
    rendered = confidence_section(
        run_id=RUN_ID, policy=DECLARED_POLICY, distribution=tally_confidence(stored, rejected)
    ).render()
    assert "not a calibrated probability" in rendered
    assert "What would reverse this statement" in rendered
    assert "frozen, hashed, labelled sample" in rendered


def test_a_run_that_computed_no_confidence_fails_rather_than_publishing() -> None:
    """FR-068 reaching the one figure that would otherwise be vacuously
    complete: a distribution over zero scores omits no admissible score."""
    with pytest.raises(ReportError, match="computed zero confidences"):
        confidence_section(
            run_id=RUN_ID, policy=DECLARED_POLICY, distribution=tally_confidence([], [])
        )


# ---------------------------------------------------------------------------
# T065 / item 7 — the failure breakdown
# ---------------------------------------------------------------------------


def test_all_seven_outcomes_are_published_including_the_zeros() -> None:
    """FR-034, SC-018: an outcome no failure took is published as a zero."""
    section = failure_breakdown_section(
        run_id=RUN_ID,
        counts={**dict.fromkeys(FAILURE_OUTCOMES, 0), "no_value_found": 4},
        attempts=120,
    )
    labels = {figure.label for figure in section.figures}
    for outcome in FAILURE_OUTCOMES:
        assert f"Failures with outcome `{outcome}`" in labels
    zeros = [figure for figure in section.figures if figure.value == 0 and figure.note is not None]
    assert len(zeros) == len(FAILURE_OUTCOMES) - 1


def test_every_failure_figure_names_the_attempt_as_its_unit() -> None:
    """FR-069: per-field outcomes are attempt-level, and the unit is on the
    figure rather than in a sentence somewhere above it."""
    section = failure_breakdown_section(run_id=RUN_ID, counts=outcome_counts(()), attempts=10)
    assert {figure.scope.unit for figure in section.figures} == {ATTEMPT_UNIT}


def test_a_missing_outcome_is_refused() -> None:
    counts = dict.fromkeys(FAILURE_OUTCOMES, 0)
    counts.pop("missing_citation")
    with pytest.raises(ReportError, match="omits"):
        failure_breakdown_section(run_id=RUN_ID, counts=counts, attempts=10)


def test_an_outcome_outside_the_seven_is_refused() -> None:
    counts = dict.fromkeys(FAILURE_OUTCOMES, 0) | {"model_declined": 1}
    with pytest.raises(ReportError, match="outside the closed set of seven"):
        failure_breakdown_section(run_id=RUN_ID, counts=counts, attempts=10)


def test_more_failures_than_attempts_is_refused() -> None:
    """Every attempt resolves to exactly one stored value or one failure, so
    failures cannot exceed attempts — the ledger would not reconcile."""
    counts = dict.fromkeys(FAILURE_OUTCOMES, 0) | {"schema_violation": 11}
    with pytest.raises(ReportError, match="does not reconcile"):
        failure_breakdown_section(run_id=RUN_ID, counts=counts, attempts=10)


def test_a_zero_attempt_denominator_is_refused() -> None:
    with pytest.raises(ReportError, match="reports none"):
        failure_breakdown_section(run_id=RUN_ID, counts=outcome_counts(()), attempts=0)


# ---------------------------------------------------------------------------
# T063 / item 13 — the attempt ledger and its counting units
# ---------------------------------------------------------------------------


def test_an_attempt_is_one_field_on_one_chunk() -> None:
    """FR-069's counting unit, applied to a corpus shape rather than described."""
    assert count_attempts(chunks_by_document={"d": 4}, attempted_fields=("a", "b", "c")) == 12


def test_a_field_absent_from_a_whole_document_is_one_attempt_for_it() -> None:
    """FR-069's stated exception, and FR-058's reason for it: attempting ten
    fields on every chunk of a five-page transmittal would otherwise produce a
    ledger dominated by structural absence."""
    total = count_attempts(
        chunks_by_document={"d": 4},
        attempted_fields=("a", "b", "c"),
        absent_fields_by_document={"d": ("c",)},
    )
    assert total == 2 * 4 + 1


def test_the_ledger_is_derived_from_the_corpus_and_not_incremented() -> None:
    """Two documents, different shapes, one declared subset."""
    assert count_attempts(
        chunks_by_document={"one": 3, "two": 1},
        attempted_fields=("a", "b"),
        absent_fields_by_document={"two": ("b",)},
    ) == 3 * 2 + (1 * 1 + 1)


def test_a_document_with_no_chunk_is_refused_rather_than_counted_as_zero() -> None:
    """Counting it as zero attempts would hide it in the denominator, which is
    how an unaccounted attempt disappears."""
    with pytest.raises(Exception, match="has nothing to attempt a field on"):
        count_attempts(chunks_by_document={"d": 0}, attempted_fields=("a",))


def test_an_absence_for_a_field_nobody_attempted_is_refused() -> None:
    with pytest.raises(Exception, match="unattempted-but-printed"):
        count_attempts(
            chunks_by_document={"d": 2},
            attempted_fields=("a",),
            absent_fields_by_document={"d": ("z",)},
        )


def test_an_empty_field_subset_is_refused() -> None:
    with pytest.raises(Exception, match="attempted zero fields"):
        count_attempts(chunks_by_document={"d": 2}, attempted_fields=())


def test_the_two_units_are_published_as_two_tables() -> None:
    """SC-018: invocation- and attempt-level figures appear as two tables.

    One invocation covers a chunk's whole declared field subset, so one
    invocation is many attempts; adjacent rows in one table would read as though
    they shared a denominator.
    """
    section = attempt_ledger_section(
        run_id=RUN_ID,
        invocations=InvocationLedger(valid=40, repaired=6, failed=2),
        attempts=AttemptLedger(attempted=480, stored=430, failed=50),
    )
    units = {figure.scope.unit for figure in section.figures}
    assert INVOCATION_UNIT in units
    assert ATTEMPT_UNIT in units
    assert all(figure.scope.unit.strip() for figure in section.figures)
    rendered = section.render()
    assert "**Invocation-level**" in rendered
    assert "**Attempt-level**" in rendered


def test_the_repaired_rate_is_published_in_its_own_right() -> None:
    """SC-018 names it separately: a run that repaired half its invocations is
    not the same run as one that repaired none, and `valid + repaired` hides
    the difference."""
    section = attempt_ledger_section(
        run_id=RUN_ID,
        invocations=InvocationLedger(valid=40, repaired=6, failed=2),
        attempts=AttemptLedger(attempted=480, stored=430, failed=50),
    )
    rates = [figure for figure in section.figures if figure.label == "Repaired rate"]
    assert len(rates) == 1
    assert rates[0].value == round(6 / 48, 6)


def test_an_unaccounted_attempt_is_published_and_fails_the_total_check() -> None:
    """FR-069: zero unaccounted for. The count is published whether or not it is
    zero — a ledger printing only its verdict would be a claim about itself."""
    section = attempt_ledger_section(
        run_id=RUN_ID,
        invocations=InvocationLedger(valid=40, repaired=6, failed=2),
        attempts=AttemptLedger(attempted=480, stored=430, failed=49),
    )
    (check,) = section.total_checks
    assert check.outcome == "FAILED"
    assert "| **unaccounted for** | 1 |" in section.render()


def test_a_reconciling_ledger_holds() -> None:
    ledger = AttemptLedger(attempted=480, stored=430, failed=50)
    assert ledger.reconciles
    assert ledger.unaccounted == 0
    section = attempt_ledger_section(
        run_id=RUN_ID,
        invocations=InvocationLedger(valid=48, repaired=0, failed=0),
        attempts=ledger,
    )
    (check,) = section.total_checks
    assert check.outcome == "held"


def test_more_resolutions_than_attempts_is_visible_as_a_negative() -> None:
    """Signed on purpose: more resolutions than attempts is a different defect
    from a lost attempt, and an absolute difference would hide which one."""
    assert AttemptLedger(attempted=10, stored=8, failed=4).unaccounted == -2


def test_a_ledger_with_no_attempt_is_refused() -> None:
    with pytest.raises(ReportError, match="zero attempts"):
        AttemptLedger(attempted=0, stored=0, failed=0)


def test_a_run_that_issued_no_invocation_is_refused() -> None:
    with pytest.raises(ReportError, match="zero invocations"):
        attempt_ledger_section(
            run_id=RUN_ID,
            invocations=InvocationLedger(valid=0, repaired=0, failed=0),
            attempts=AttemptLedger(attempted=10, stored=10, failed=0),
        )


def test_the_repaired_rate_has_no_denominator_on_an_empty_run() -> None:
    """Undefined, not zero. Publishing it as zero would report a run that
    repaired nothing."""
    with pytest.raises(ReportError, match="no denominator"):
        _ = InvocationLedger(valid=0, repaired=0, failed=0).repaired_rate
