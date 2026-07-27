"""TR-034: the per-request deadline is the gateway's, and it is 120 seconds."""

from __future__ import annotations

import pytest

from gateway.config import (
    DEADLINE_ENV_VAR,
    DEFAULT_REQUEST_DEADLINE_SECONDS,
    OTEL_GENAI_SEMCONV_VERSION,
    GatewayConfig,
    load_config,
)
from gateway.errors import GatewayConfigError, GatewayError


def test_the_default_deadline_is_the_one_the_requirement_states() -> None:
    """Pinned to the number rather than to the constant, which would compare
    the constant to itself."""
    assert load_config({}).request_deadline_seconds == 120.0
    assert DEFAULT_REQUEST_DEADLINE_SECONDS == 120.0


def test_the_deadline_is_read_from_the_environment() -> None:
    assert load_config({DEADLINE_ENV_VAR: "30"}).request_deadline_seconds == 30.0


def test_an_absent_variable_takes_the_default() -> None:
    """An absent setting is a choice not made, and the documented default is
    the answer. This is the one fallback in the loader, and it is deliberate —
    unlike a *malformed* value, which is a choice made wrongly."""
    assert load_config({"UNRELATED": "x"}).request_deadline_seconds == 120.0


@pytest.mark.parametrize("value", ["", "soon", "12s", "1,5"])
def test_an_unparseable_deadline_fails_rather_than_falling_back(value: str) -> None:
    """Falling back would let a typo run for months under a deadline nobody
    chose, and the run would look entirely normal."""
    with pytest.raises(GatewayConfigError, match=DEADLINE_ENV_VAR):
        load_config({DEADLINE_ENV_VAR: value})


@pytest.mark.parametrize("value", ["0", "-1", "-0.5"])
def test_a_non_positive_deadline_is_rejected(value: str) -> None:
    """Zero would expire before the first attempt and turn every invocation
    into a deadline failure — a configuration that fails everything is worth
    catching at load rather than at the first call."""
    with pytest.raises(GatewayConfigError, match="greater than zero"):
        load_config({DEADLINE_ENV_VAR: value})


def test_the_configuration_error_is_gateway_owned() -> None:
    """A caller catching GatewayError catches it; one catching pydantic's
    ValidationError would be catching a dependency's type (TR-002)."""
    with pytest.raises(GatewayError):
        load_config({DEADLINE_ENV_VAR: "0"})


def test_the_message_names_the_key_and_the_offending_value() -> None:
    """TR-065 bounds message content for the *credential* key and values read
    from it. A deadline is neither, and withholding the value would cost the
    reader the one fact that identifies the typo."""
    with pytest.raises(GatewayConfigError) as raised:
        load_config({DEADLINE_ENV_VAR: "12s"})
    assert DEADLINE_ENV_VAR in str(raised.value)
    assert "12s" in str(raised.value)


def test_the_configuration_is_frozen() -> None:
    """Configuration that can change mid-invocation makes the deadline a moving
    target and the record's account of it a guess."""
    config = GatewayConfig()
    with pytest.raises(Exception, match="frozen|immutable"):
        config.request_deadline_seconds = 5.0  # type: ignore[misc]


def test_an_unknown_field_is_rejected() -> None:
    """`extra="forbid"`: a misspelled field name would otherwise be accepted
    and silently ignored, leaving the real one at its default."""
    with pytest.raises(Exception, match="extra"):
        GatewayConfig(request_deadline_second=5.0)  # type: ignore[call-arg]


def test_the_loader_does_not_consult_the_process_environment_when_given_one() -> None:
    """The explicit mapping is the whole mechanism: a loader that fell back to
    `os.environ` for absent keys would make every test's result depend on the
    developer's shell."""
    assert load_config({}).request_deadline_seconds == DEFAULT_REQUEST_DEADLINE_SECONDS


# --- TR-070 (T026): the pinned semantic-convention version -------------------


def test_the_pin_is_the_version_verified_against_the_published_registry() -> None:
    """T026's verification, frozen as an assertion.

    1.36.0 was the original pin and it was wrong: that release defines
    `gen_ai.system`, not `gen_ai.provider.name` — the attribute the pin was
    chosen for. Pinned to the number rather than to the constant, which would
    compare the constant to itself and pass at any value.
    """
    assert OTEL_GENAI_SEMCONV_VERSION == "1.37.0"
    assert GatewayConfig().otel_genai_semconv_version == "1.37.0"


def test_the_pin_is_not_readable_from_the_environment() -> None:
    """A pin an operator can move without a migration is not a pin.

    The column names in a migrated database follow whatever version was pinned
    when `0102` was written; an environment variable that changed the claim
    without changing the columns would make the configuration lie about the
    schema.
    """
    config = load_config({"GATEWAY_OTEL_GENAI_SEMCONV_VERSION": "9.9.9"})
    assert config.otel_genai_semconv_version == OTEL_GENAI_SEMCONV_VERSION


def test_the_pin_is_a_concrete_version_not_a_placeholder() -> None:
    """TR-070 requires a concrete version so OBJ3 VC7 has a fixed referent.

    Shape-checked rather than pattern-matched loosely: `latest`, `main`, an
    empty string or a bare major would each satisfy "is a string" while
    defeating the requirement's whole purpose.
    """
    parts = OTEL_GENAI_SEMCONV_VERSION.split(".")
    assert len(parts) == 3, f"not a three-part version: {OTEL_GENAI_SEMCONV_VERSION!r}"
    assert all(part.isdigit() for part in parts), (
        f"not a concrete numeric version: {OTEL_GENAI_SEMCONV_VERSION!r}"
    )
