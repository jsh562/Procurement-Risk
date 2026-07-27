"""TR-005 through TR-009, TR-042, TR-078: validation, the single repair, outcome.

VR-034 asks for a table-driven test over *every reachable combination* of
terminal state and attempt counts. That phrasing is doing work: a test that
picks three representative cases can pass while an unreachable-looking fourth
combination is silently misclassified, and the combination TR-078 singles out —
an invocation that consumed transport retries and *then* repaired successfully
— is exactly the one a hand-picked set omits. So the table below is generated
from the input domains rather than written out, and its size is asserted.
"""

from __future__ import annotations

import itertools

import pytest
from pydantic import BaseModel, Field

from gateway import provider
from gateway.errors import GatewayValidationError
from gateway.models import Outcome
from gateway.orchestrator import classify_outcome, record_then_raise
from gateway.provider import MAX_TRANSPORT_ATTEMPTS
from gateway.validation import (
    MAX_REPAIR_ATTEMPTS,
    ValidationFailure,
    repair_instruction,
    residual_constraints,
    validate_or_repair,
)


class Assessment(BaseModel):
    """A caller's schema, carrying constraints on both sides of the line.

    `score` has bounds and `label` has a length floor — neither survives into
    the native mode, which is what makes this a realistic test of the
    post-decode step rather than of pydantic.
    """

    label: str = Field(min_length=3)
    score: int = Field(ge=1, le=10)


# --- TR-009 / TR-042 / TR-078: the outcome mapping, over the whole domain -----

#: Every combination the two inputs admit. Transport attempts are included in
#: the table and deliberately *not* passed to the classifier: TR-078 says the
#: classification holds "whatever the transport attempt count", and the way to
#: test a claim of independence is to vary the thing it claims independence
#: from.
OUTCOME_TABLE = list(
    itertools.product(
        (True, False),  # reached a schema-valid value
        range(MAX_REPAIR_ATTEMPTS + 1),  # repair attempts consumed
        range(1, MAX_TRANSPORT_ATTEMPTS + 1),  # transport attempts consumed
    )
)


def test_the_table_covers_every_reachable_combination() -> None:
    """VR-034's denominator, asserted rather than assumed.

    Without this the table could shrink to one row — through a typo in a range
    bound, or a budget constant changing — and every parametrized case below
    would still pass, reporting full coverage of a domain of one.
    """
    expected = 2 * (MAX_REPAIR_ATTEMPTS + 1) * MAX_TRANSPORT_ATTEMPTS
    assert len(OUTCOME_TABLE) == expected, (
        f"the outcome table has {len(OUTCOME_TABLE)} rows, expected {expected}"
    )
    assert len(OUTCOME_TABLE) == len(set(OUTCOME_TABLE)), "the table repeats a combination"


@pytest.mark.parametrize(("valid", "repairs", "transports"), OUTCOME_TABLE)
def test_every_combination_maps_to_exactly_one_outcome(
    valid: bool, repairs: int, transports: int
) -> None:
    """TR-078: total and exhaustive. Every row lands, and lands on one value."""
    outcome = classify_outcome(reached_valid_value=valid, repair_attempt_count=repairs)
    assert outcome in {"valid", "repaired", "failed"}, (
        f"({valid}, {repairs}, {transports}) produced {outcome!r}, which is outside "
        f"TR-009's three values"
    )

    if not valid:
        expected = "failed"
    elif repairs == 0:
        expected = "valid"
    else:
        expected = "repaired"
    assert outcome == expected, (
        f"valid={valid}, repairs={repairs}, transports={transports} classified as "
        f"{outcome!r}, expected {expected!r} (TR-078)"
    )


@pytest.mark.parametrize("transports", range(1, MAX_TRANSPORT_ATTEMPTS + 1))
def test_a_transport_failure_is_never_repaired(transports: int) -> None:
    """TR-009's negative rule, held at every point in the transport budget.

    A transport failure reaches no valid value, so it cannot be `repaired` —
    and the reason it cannot is structural rather than guarded: the classifier
    is not given the transport count at all, so there is no branch where a
    retry could turn into a repair.
    """
    assert classify_outcome(reached_valid_value=False, repair_attempt_count=0) == "failed"
    assert classify_outcome(reached_valid_value=False, repair_attempt_count=1) == "failed"


def test_the_combination_the_negative_rule_leaves_unclassified() -> None:
    """TR-078 names this case specifically: retries consumed, then a successful
    repair. The negative rule alone says only what it is not."""
    assert classify_outcome(reached_valid_value=True, repair_attempt_count=1) == "repaired"


@pytest.mark.parametrize("repairs", [-1, MAX_REPAIR_ATTEMPTS + 1])
def test_a_repair_count_outside_the_budget_is_rejected(repairs: int) -> None:
    """Silently classifying an impossible count would hide the miscount that
    produced it, and TR-007's budget is the thing being miscounted."""
    with pytest.raises(ValueError, match="TR-007"):
        classify_outcome(reached_valid_value=True, repair_attempt_count=repairs)


# --- TR-006 / TR-007 / TR-008: validate, repair once, fail closed ------------


def test_a_valid_first_response_consumes_no_repair() -> None:
    """TR-010's "exactly one request" has a counterpart here: a first response
    that validates must not trigger a speculative repair."""

    def repair(_: str) -> str:
        raise AssertionError("a valid first response must not be repaired")

    value, repairs = validate_or_repair(Assessment, '{"label":"ok!","score":5}', repair)
    assert value.score == 5
    assert repairs == 0


def test_one_repair_is_attempted_and_carries_the_field_path() -> None:
    """TR-007: at most one repair, carrying the failing field path *and* the
    validation message."""
    seen: list[str] = []

    def repair(instruction: str) -> str:
        seen.append(instruction)
        return '{"label":"fine","score":5}'

    value, repairs = validate_or_repair(Assessment, '{"label":"fine","score":99}', repair)
    assert repairs == 1
    assert value.score == 5
    assert len(seen) == 1, "more than one repair was attempted"
    assert "score" in seen[0], f"the repair did not carry the failing field path: {seen[0]}"
    assert "less than or equal to 10" in seen[0], (
        f"the repair did not carry the validation message: {seen[0]}"
    )


def test_a_second_failure_fails_closed() -> None:
    """TR-008. No third attempt, and no value returned."""
    attempts: list[str] = []

    def repair(instruction: str) -> str:
        attempts.append(instruction)
        return '{"label":"x","score":0}'

    with pytest.raises(GatewayValidationError) as raised:
        validate_or_repair(Assessment, '{"label":"x","score":0}', repair)

    assert len(attempts) == MAX_REPAIR_ATTEMPTS, (
        f"{len(attempts)} repairs attempted, TR-007 permits {MAX_REPAIR_ATTEMPTS}"
    )
    assert raised.value.repair_attempt_count == MAX_REPAIR_ATTEMPTS
    assert set(raised.value.field_paths) == {"label", "score"}


def test_the_failure_does_not_carry_the_rejected_value() -> None:
    """TR-006 forbids returning an unvalidated value. An error carrying the
    model's output would return it by a quieter route — the caller reads it off
    the exception instead of off the return."""
    rejected = "sentinel-completion-text"

    def repair(_: str) -> str:
        return f'{{"label":"{rejected}","score":0}}'

    with pytest.raises(GatewayValidationError) as raised:
        validate_or_repair(Assessment, f'{{"label":"{rejected}","score":0}}', repair)

    rendered = str(raised.value) + repr(raised.value.field_paths)
    assert rejected not in rendered, f"the rejected value leaked into the error: {rendered}"


def test_output_that_is_not_json_is_a_validation_failure() -> None:
    """The most unvalidated a value gets. Handled as a validation failure
    rather than its own category, because there is nothing different to do
    about it — and it must still consume the repair, not bypass it."""
    calls: list[str] = []

    def repair(instruction: str) -> str:
        calls.append(instruction)
        return '{"label":"good","score":3}'

    value, repairs = validate_or_repair(Assessment, "I'm sorry, I can't do that", repair)
    assert repairs == 1
    assert value.label == "good"
    assert "not valid JSON" in calls[0]


def test_the_repair_instruction_carries_no_completion_content() -> None:
    """TR-026 / TR-066: the instruction is logged and may reach the record, so
    it must be built from paths and validator messages alone."""
    instruction = repair_instruction(
        [ValidationFailure("score", "Input should be less than or equal to 10")]
    )
    assert "score" in instruction
    assert "less than or equal to 10" in instruction


# --- TR-008: the record is written *before* the error is raised --------------


def test_the_record_is_written_before_the_error_is_raised() -> None:
    """TR-008's ordering, which a natural implementation gets backwards.

    Raising where the failure happens and recording in an `except` further out
    reads perfectly well and leaves the record unwritten on every path that
    re-raises before reaching it. Asserted by observing the write, not by
    reading the code.
    """
    written: list[tuple[str, str]] = []

    def write(*, trace_id: str, outcome: Outcome, error_type: str | None) -> None:
        written.append((trace_id, outcome))

    error = GatewayValidationError("no valid value", field_paths=("score",))
    with pytest.raises(GatewayValidationError):
        record_then_raise(
            error,
            write=write,
            trace_id="a" * 32,
            outcome="failed",
            error_type="validation_failed",
        )

    assert written == [("a" * 32, "failed")], (
        "the invocation record was not written before the error was raised (TR-008)"
    )


def test_a_write_failure_does_not_replace_the_original_error() -> None:
    """The caller receives the error describing *their* invocation.

    Letting the storage failure win would tell a caller their database is
    unreachable when what actually happened is that the model produced nothing
    valid — a true statement about the wrong thing.
    """

    def write(*, trace_id: str, outcome: Outcome, error_type: str | None) -> None:
        raise RuntimeError("database unreachable")

    error = GatewayValidationError("no valid value", field_paths=("score",))
    with pytest.raises(GatewayValidationError) as raised:
        record_then_raise(
            error, write=write, trace_id="b" * 32, outcome="failed", error_type=None
        )

    assert "no valid value" in str(raised.value)
    notes = getattr(raised.value, "__notes__", [])
    assert any("database unreachable" in note for note in notes), (
        f"the write failure was lost rather than attached: {notes}"
    )


def test_the_write_failure_note_holds_no_reference_to_its_exception() -> None:
    """TR-025 closes the gateway error's field set at three scalars. A note
    carrying the exception object would put a fourth thing in reach of a
    `repr`, and the note is rendered wherever the error is."""

    def write(*, trace_id: str, outcome: Outcome, error_type: str | None) -> None:
        raise RuntimeError("boom")

    error = GatewayValidationError("no valid value")
    with pytest.raises(GatewayValidationError) as raised:
        record_then_raise(
            error, write=write, trace_id="c" * 32, outcome="failed", error_type=None
        )

    assert all(isinstance(note, str) for note in getattr(raised.value, "__notes__", []))


# --- TR-005: what the native mode could not carry ----------------------------


def test_the_provider_transform_is_reachable() -> None:
    """The fragility, observed rather than tolerated.

    `native_output_schema` reads the SDK's transform from a private module path
    and returns None if it moves. Without this test that upgrade would turn
    every residual list silently empty and nothing would report it — the
    disclosure would keep passing by having nothing to disclose.
    """
    transformed = provider.native_output_schema(Assessment)
    assert transformed is not None, (
        "the provider SDK no longer exposes its schema transform where "
        "native_output_schema looks for it; residual-constraint disclosure is "
        "silently degraded until the path is updated"
    )


def test_the_native_mode_drops_the_callers_bounds() -> None:
    """TR-005's premise, asserted against the SDK rather than assumed.

    If this ever fails because the mode gained support for these keywords, the
    post-decode step becomes redundant for them — which is worth knowing, and
    is not something to discover by reading a changelog.
    """
    original = Assessment.model_json_schema()
    transformed = provider.native_output_schema(Assessment)
    assert transformed is not None

    residuals = residual_constraints(original, transformed)
    keywords = {residual.keyword for residual in residuals}
    assert {"minimum", "maximum", "minLength"} <= keywords, (
        f"expected the caller's bounds to survive only as residuals; got {sorted(keywords)}"
    )


def test_the_residuals_name_where_the_constraint_was() -> None:
    """A keyword without a location is unactionable in a schema of any size."""
    original = Assessment.model_json_schema()
    transformed = provider.native_output_schema(Assessment)
    assert transformed is not None

    residuals = residual_constraints(original, transformed)
    pointers = {r.pointer for r in residuals if r.keyword == "maximum"}
    assert pointers == {"/properties/score"}, f"maximum was reported at {pointers}"


def test_a_schema_the_mode_carries_whole_has_no_residuals() -> None:
    """The negative direction. Without it, a walk that reported *every* keyword
    would pass all the tests above and be useless."""

    class Plain(BaseModel):
        name: str

    transformed = provider.native_output_schema(Plain)
    assert transformed is not None
    assert residual_constraints(Plain.model_json_schema(), transformed) == ()


def test_one_of_is_reported_even_though_a_key_of_that_name_survives() -> None:
    """`oneOf` becomes `anyOf`: exactly-one becomes at-least-one. A walk
    comparing key presence would call that a rename and report nothing."""
    original = {"type": "object", "oneOf": [{"type": "object"}]}
    transformed = {"type": "object", "anyOf": [{"type": "object"}]}
    keywords = {r.keyword for r in residual_constraints(original, transformed)}
    assert "oneOf" in keywords, "a weakened constraint was read as a rename"


def test_additional_properties_forced_shut_is_not_a_residual() -> None:
    """Stricter is not dropped. Reporting it would train readers to skim the
    list, which costs more than the entry is worth."""
    original = {"type": "object", "additionalProperties": True}
    transformed = {"type": "object", "additionalProperties": False}
    assert residual_constraints(original, transformed) == ()
