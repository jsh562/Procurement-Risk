"""FR-063: the parse-signal row the writer produces, and the guard before it.

T059's unit half. The storage half — that a signal row's inputs recompute the
stored score exactly, under the run's own weights — is
`src/model/tests/schema/test_parse_signals.py`, which needs a database. What is
checked here needs none:

* the column list the writer writes is the column list revision `0403` declares,
  compared rather than trusted, so a column added to the table and not to the
  statement fails here instead of at the first insert of a real run;
* the score and its signals are checked to agree **before** either is written,
  at bit equality and against the run's own weights;
* a score below the run's declared floor is refused rather than stored (FR-032).

**No weight and no floor is written out in this file.** The declared policy is
imported from `model.ingest.runs`, which is where the declaration lives and what
the run row carries. A test naming `0.15` would keep passing after the row moved.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from model.compute.confidence import DeductionWeights, ParseSignals, compute_confidence
from model.ingest.runs import DECLARED_POLICY, ConfidencePolicy
from model.ingest.writer import (
    PARSE_SIGNAL_COLUMNS,
    CitedChunk,
    PreparedValue,
    WriterError,
    check_confidence_agrees,
    cite_value,
)

#: `.../src/model/tests/ingest/test_parse_signal_write.py` — three levels up is
#: `src/model`. Resolved from `__file__` so the read works from any cwd.
ENTRY_ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ENTRY_ROOT / "src" / "model" / "schema" / "versions"
SIGNAL_REVISION = VERSIONS / "0403_value_associations.py"


def declared_columns() -> tuple[str, ...]:
    """The `extracted_value_parse_signal` columns revision `0403` creates.

    Read from the revision's own `CREATE TABLE` rather than from a live catalog:
    the comparison is between the writer's statement and the schema's
    declaration, and requiring a migrated database to make it would leave the
    statement unchecked on every run that has no server — which is most of them.
    """
    source = SIGNAL_REVISION.read_text(encoding="utf-8")
    body = source.split("CREATE TABLE extracted_value_parse_signal (", 1)[1]
    body = body.split("CONSTRAINT", 1)[0]
    return tuple(
        match.group(1)
        for line in body.splitlines()
        if (match := re.match(r"\s{12}([a-z_]+)\s+(uuid|text|smallint|boolean)\b", line))
    )


def prepared(
    *,
    signals: ParseSignals,
    confidence: float,
    contributors: tuple[CitedChunk, ...] = (),
) -> PreparedValue:
    """One value carrying `signals`, cited from a chunk consistent with them."""
    return PreparedValue(
        field_name="manufacturer",
        value_kind="text",
        value_text="Norhelm Transformer Wks.",
        value_number=None,
        confidence=confidence,
        citation=cite_value(CitedChunk(3, 2), contributors),
        signals=signals,
    )


def test_the_written_columns_are_the_columns_the_revision_declares() -> None:
    """A column added to the table and not to the statement is a NOT NULL
    violation on the first real insert; here it is a failing test with a diff."""
    assert set(PARSE_SIGNAL_COLUMNS) == set(declared_columns())
    assert len(PARSE_SIGNAL_COLUMNS) == 6


def test_the_signal_row_names_no_confidence_column() -> None:
    """`data-model.md`: the score is `extracted_value.confidence`, E003's.

    Copying it beside its inputs would create the one thing SC-026 exists to
    detect — a stored number that can drift from the signals it claims to be
    computed from. The recomputation reads the signals here and the score there,
    one join apart.
    """
    assert "confidence" not in PARSE_SIGNAL_COLUMNS


def test_an_agreeing_score_and_signal_pair_passes() -> None:
    signals = ParseSignals("alternate", 1, False)
    value = prepared(
        signals=signals, confidence=compute_confidence(signals, DECLARED_POLICY.weights)
    )
    check_confidence_agrees(value, DECLARED_POLICY)


def test_a_score_that_disagrees_with_its_signals_is_refused() -> None:
    """SC-026 asserted at the write boundary rather than only afterwards.

    A value whose stored score does not follow from its stored signals is a
    number nothing can explain, which is what Principle I calls a defect rather
    than a rough edge.
    """
    signals = ParseSignals("alternate", 1, False)
    wrong = compute_confidence(ParseSignals("canonical", 1, False), DECLARED_POLICY.weights)
    with pytest.raises(WriterError, match="SC-026"):
        check_confidence_agrees(prepared(signals=signals, confidence=wrong), DECLARED_POLICY)


def test_the_comparison_is_bit_equality_and_not_a_tolerance() -> None:
    """The declared application order only means something under exact equality.

    A score one unit in the last place away from the computed one is refused;
    accepting it would accept exactly the grouping error the declared left-to-
    right order exists to exclude.
    """
    signals = ParseSignals("alternate", 2, False)
    exact = compute_confidence(signals, DECLARED_POLICY.weights)
    nudged = exact + 2**-52
    assert nudged != exact
    with pytest.raises(WriterError, match="bit equality"):
        check_confidence_agrees(
            prepared(signals=signals, confidence=nudged, contributors=(CitedChunk(2, 1),)),
            DECLARED_POLICY,
        )


def test_the_weights_that_decide_agreement_are_the_ones_passed_in() -> None:
    """A score is checked against the policy that produced it, never today's.

    The same value passes under the policy it was scored with and fails under
    another — which is the whole reason the writer reads the run's row rather
    than importing the declared constants.
    """
    other = ConfidencePolicy(
        floor=0.65,
        weights=DeductionWeights(alternate_label=0.3, page_split=0.1, repaired=0.45),
    )
    signals = ParseSignals("alternate", 1, False)
    value = prepared(signals=signals, confidence=compute_confidence(signals, other.weights))
    check_confidence_agrees(value, other)
    with pytest.raises(WriterError, match="SC-026"):
        check_confidence_agrees(value, DECLARED_POLICY)


def test_a_score_below_the_runs_floor_is_not_stored() -> None:
    """FR-032, Principle III: recorded absent rather than stored wrong.

    Reaching the writer with a below-floor value means the orchestrator tried to
    persist one; it belongs in `extraction_failure` with outcome
    `confidence_below_threshold`.
    """
    signals = ParseSignals("alternate", 2, True)
    value = prepared(
        signals=signals,
        confidence=compute_confidence(signals, DECLARED_POLICY.weights),
        contributors=(CitedChunk(2, 1),),
    )
    assert not DECLARED_POLICY.admits(value.confidence)
    with pytest.raises(WriterError, match="confidence_below_threshold"):
        check_confidence_agrees(value, DECLARED_POLICY)
