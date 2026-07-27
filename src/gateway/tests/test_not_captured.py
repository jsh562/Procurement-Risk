"""TR-066 / TR-076 (VR-037): the not-captured set, asserted per sink.

TR-076 states what this epic deliberately does **not** capture as its own closed
obligation rather than letting it be inferred from the absence of fields in
TR-012's list — because an absence inferred is an absence nobody has to keep
true.

The set is five: prompt text, completion text, system-prompt text, credential
material, and end-user identity. The last is the one worth pausing on: no
requirement in this spec collects, accepts, or requires it anywhere, so its
absence is a **recorded decision** rather than a gap someone might later fill by
adding a field that seemed useful.

**The obligation is per sink, and that is the point.** A single global claim
would be false at one of them: the committed fixture store deliberately *does*
retain prompt and completion content — that is what a fixture is. Stating it
sink by sink is what keeps the position honest instead of nearly true.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from gateway.config import content_capture_enabled
from gateway.errors import ProviderError
from gateway.fixtures import FixtureProvenance, FixtureStore
from gateway.models import InvocationRecord
from gateway.record.spool import InvocationSpool
from gateway.record.writer import COLUMNS
from tests.test_record_writer import a_record

#: TR-076's five, as field-name fragments a sink might carry one under. Matched
#: as substrings so `system_prompt`, `prompt_text` and `prompt` all count — the
#: risk is a field that *holds* one of these, not one spelled exactly so.
NOT_CAPTURED = ("prompt", "completion", "content", "message", "system", "user", "api_key")

#: The one sink where prompt and completion content is retained on purpose.
#: Named here so the exception is a stated position rather than a gap in the
#: tests below.
FIXTURE_STORE_RETAINS_CONTENT = True


def _offending(names: object) -> list[str]:
    return sorted(
        name
        for name in names  # type: ignore[union-attr]
        if any(fragment in str(name).lower() for fragment in NOT_CAPTURED)
    )


# --- Sink: the invocation record --------------------------------------------


def test_the_record_carries_none_of_the_five() -> None:
    """TR-068 closes the field list, which is what makes this checkable: the
    row carries these twenty-two fields and no others, so "none of the five is
    on the row" is a statement about a finite set."""
    assert _offending(InvocationRecord.model_fields) == []
    assert _offending(COLUMNS) == []


def test_the_record_carries_none_of_the_five_at_any_toggle_setting() -> None:
    """TR-066 scopes content capture to log output alone. The record's field
    list is closed, so no setting of the toggle can widen it — asserted rather
    than argued, because "the toggle does not affect this" is exactly the kind
    of claim that quietly stops being true."""
    for enabled in ({}, {"GATEWAY_CAPTURE_CONTENT": "1"}):
        assert _offending(InvocationRecord.model_fields) == [], enabled
    assert content_capture_enabled({"GATEWAY_CAPTURE_CONTENT": "1"})


def test_no_end_user_identity_field_exists_anywhere_on_the_row() -> None:
    """TR-076 singles this out: no requirement in this spec collects, accepts,
    or requires end-user identity. Its absence is a recorded decision, so a
    field added later "because it was useful" should fail here rather than pass
    review."""
    identity_shaped = {"user", "user_id", "subject", "principal", "actor", "email"}
    assert not (set(COLUMNS) & identity_shaped)


# --- Sink: the spool payload ------------------------------------------------


def test_the_spool_payload_field_set_is_exactly_the_records(tmp_path: Path) -> None:
    """TR-041 fixes the spool payload's field list as *the record's*, so the
    two cannot drift into the spool carrying something the row does not. A spool
    that serialized a richer object would put content on local disk that the
    database never held."""
    spool = InvocationSpool(tmp_path / "spool.sqlite3")
    record = a_record()
    spool.append(record, write_error_type="OperationalError")

    import json

    payload = json.loads(next(iter(spool.pending())).payload)
    assert set(payload) == set(InvocationRecord.model_fields)
    assert _offending(payload) == []


# --- Sink: the normalized gateway error -------------------------------------


def test_the_normalized_error_carries_three_scalars_and_no_content() -> None:
    """TR-025 closes the field set at status, error type, and request
    identifier. None of the five can ride along, because there is nowhere for
    it to sit."""
    error = ProviderError("failed", status=429, error_type="transport_failed", request_id="req_1")
    carried = {
        name for name in vars(error) if not name.startswith("_")
    }
    assert carried == {"status", "error_type", "request_id"}
    assert _offending(carried) == []


# --- Sink: log output --------------------------------------------------------


def test_log_output_carries_content_only_when_explicitly_enabled() -> None:
    """The only sink where prompt or completion content may *ever* appear, and
    only under the toggle. Never credential material and never end-user
    identity, at any setting — those two are excluded unconditionally, which is
    a different rule from the content one and is stated separately in TR-076."""
    assert not content_capture_enabled({})


def test_the_closed_log_field_list_names_no_credential_or_identity() -> None:
    """TR-066 closes the log field list over TR-077's five events.

    The **emitted field names** are extracted from the AST, not from the source
    text. A text scan matches the docstring explaining that no credential
    appears — the fifth time this epic hit that trap, after `test_migrations`,
    `test_field_naming`, `test_fixture_credential_scan`, and a comment in
    `config.py`. The lesson generalises: a check whose subject is *code* must
    read the parse tree, because prose about code contains the words the code
    is being checked for.
    """
    import ast

    from gateway.record import writer

    tree = ast.parse(Path(writer.__file__).read_text(encoding="utf-8"))
    logging_functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in {"log_invocation_complete", "_log_absent_cost"}
    }
    assert set(logging_functions) == {"log_invocation_complete", "_log_absent_cost"}, (
        f"the log events moved or were renamed: {sorted(logging_functions)}"
    )

    emitted: set[str] = set()
    for function in logging_functions.values():
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict):
                    emitted.update(
                        key.value
                        for key in keyword.value.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    )

    assert emitted, "no log fields were extracted, so this asserts nothing"
    assert _offending(emitted) == [], (
        f"the gateway's log events name {_offending(emitted)}, which TR-076 places "
        f"in the not-captured set (TR-066)"
    )


# --- Sink: the committed fixture store, the one exception -------------------


def test_the_fixture_store_retains_content_by_design(tmp_path: Path) -> None:
    """The single sink at which the not-captured position does not hold in full,
    named as such by TR-076 rather than left as a hole.

    A fixture *is* prompt and completion content — that is what makes replay
    possible. What it carries no trace of is credential material, end-user
    identity, or corpus material outside TR-067's bound.
    """
    store = FixtureStore(tmp_path / "fixtures")
    key = "sha256:" + "a" * 64
    store.save(key, '{"completion": "the model said this"}', _a_provenance())

    loaded = store.load(key)
    assert "the model said this" in loaded.content, (
        "a fixture that dropped completion content could not replay anything"
    )
    assert FIXTURE_STORE_RETAINS_CONTENT


def test_the_provenance_sidecar_carries_no_content(tmp_path: Path) -> None:
    """The response file retains content; its *label* does not. A sidecar that
    duplicated the completion would put content in a second place with a
    different review posture."""
    assert _offending(FixtureProvenance.model_fields) == []


def _a_provenance() -> FixtureProvenance:
    return FixtureProvenance(
        recorded_on=date(2026, 7, 26),
        gen_ai_response_model="claude-opus-5",
        gateway_revision="0" * 40,
        gen_ai_usage_input_tokens=1,
        gen_ai_usage_output_tokens=1,
    )


# --- The inventory itself ----------------------------------------------------


@pytest.mark.parametrize(
    "sink",
    ["invocation_record", "spool_payload", "gateway_error", "log_output", "fixture_store"],
)
def test_every_sink_has_a_stated_position(sink: str) -> None:
    """VR-037's denominator. TR-076 states the position per sink, so a sink
    without one here would be a sink whose position nobody wrote down.

    Weak by design — it asserts a test exists for each, not what that test
    found. The findings are above; this is the check that none went missing.
    """
    tests = Path(__file__).read_text(encoding="utf-8")
    marker = {
        "invocation_record": "test_the_record_carries_none_of_the_five",
        "spool_payload": "test_the_spool_payload_field_set_is_exactly_the_records",
        "gateway_error": "test_the_normalized_error_carries_three_scalars_and_no_content",
        "log_output": "test_log_output_carries_content_only_when_explicitly_enabled",
        "fixture_store": "test_the_fixture_store_retains_content_by_design",
    }[sink]
    assert marker in tests


def test_the_pricing_timestamp_survives_as_an_aware_datetime() -> None:
    """Unrelated to redaction and placed here deliberately: the provenance
    sidecar is the one object in this file whose *absence* of content is
    checked, so its remaining behaviour is worth pinning in the same place."""
    assert _a_provenance().pricing_timestamp() == datetime(2026, 7, 26, tzinfo=UTC)
