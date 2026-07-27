"""TR-062 / TR-065 / TR-026: the credential key, and what a failure may say.

`test_fixtures.py` covers mode selection and the opt-in as *behaviour*. This
file covers the two properties that are about the credential specifically: that
there is exactly one key and it is matched exactly, and that a configuration
failure names the key without carrying anything derived from its value.
"""

from __future__ import annotations

import pytest

from gateway.config import (
    CONTENT_CAPTURE_ENABLED_VALUE,
    CONTENT_CAPTURE_ENV_VAR,
    CREDENTIAL_ENV_VAR,
    configuration_failure_message,
    content_capture_enabled,
    credential_is_present,
)

SECRET = "a-configured-credential-value"


# --- TR-062: exactly one key, matched exactly -------------------------------


def test_the_credential_key_is_the_one_the_sdk_resolves() -> None:
    """Chosen so TR-023's guard and the SDK's own resolution cannot disagree.

    A gateway-specific name would let the guard see nothing while the SDK found
    a key by its own convention — the guard would pass and the call would go
    out.
    """
    assert CREDENTIAL_ENV_VAR == "ANTHROPIC_API_KEY"


def test_a_present_value_is_present() -> None:
    assert credential_is_present({CREDENTIAL_ENV_VAR: SECRET})


@pytest.mark.parametrize("value", ["", " ", "\t", "\n  "])
def test_a_blank_value_is_absent(value: str) -> None:
    """An exported-but-blank variable is a broken shell far more often than a
    considered instruction, and treating it as present would refuse `replay`
    mode on a machine that has no credential at all."""
    assert not credential_is_present({CREDENTIAL_ENV_VAR: value})


def test_an_unset_key_is_absent() -> None:
    assert not credential_is_present({})


@pytest.mark.parametrize(
    "neighbour",
    [
        "ANTHROPIC_API_KEY_OLD",
        "MY_ANTHROPIC_API_KEY",
        "anthropic_api_key",
        "ANTHROPIC_AUTH_TOKEN",
    ],
)
def test_no_neighbouring_name_is_consulted(neighbour: str) -> None:
    """TR-062: no prefix match, no case-insensitive match, no scan of
    neighbouring names — so the guard has one checkable subject rather than an
    intent, and cannot be satisfied or defeated by a name nobody named."""
    assert not credential_is_present({neighbour: SECRET})


def test_the_disclosed_limit_is_that_only_this_environment_is_observed() -> None:
    """TR-062 states the limit rather than leaving it implied, so this asserts
    the shape that makes it true: the check reads a mapping and nothing else.

    A credential reaching the SDK by another path — a configuration file, an
    explicit argument, a platform keychain — is neither observed nor blocked.
    `replay` mode's no-credential property is a property of *this* environment,
    not a proof that no credential exists anywhere the process could reach.
    """
    import inspect

    parameters = list(inspect.signature(credential_is_present).parameters)
    assert parameters == ["env"], (
        f"credential_is_present takes {parameters}; anything beyond the mapping "
        f"would widen a check TR-062 deliberately scopes"
    )


# --- TR-065: what a configuration failure may say ---------------------------


def test_a_failure_message_names_the_key() -> None:
    """Naming the key is what makes the message actionable. A message that
    named neither key nor value would be safe and useless."""
    assert CREDENTIAL_ENV_VAR in configuration_failure_message(CREDENTIAL_ENV_VAR)


def test_the_credential_key_admits_no_detail_at_all() -> None:
    """The exclusion set is not "no value" — it is no substring, truncation,
    prefix, suffix, hash, or length, and no other value read from the key.

    Refusing the parameter is stronger than redacting it: a redaction step can
    be forgotten, and an argument that is rejected cannot be.
    """
    with pytest.raises(ValueError, match="TR-065"):
        configuration_failure_message(CREDENTIAL_ENV_VAR, detail="starts with sk-")

    with pytest.raises(ValueError):
        configuration_failure_message(CREDENTIAL_ENV_VAR, detail="29 characters")


def test_a_non_credential_key_may_carry_detail() -> None:
    """The exclusion is scoped to the credential key and values read from it.
    A malformed deadline's value is neither, and withholding it would cost the
    reader the one fact that identifies the typo."""
    message = configuration_failure_message("GATEWAY_MODE", detail="got 'Record'")
    assert "GATEWAY_MODE" in message
    assert "Record" in message


def test_the_value_is_never_a_parameter() -> None:
    """The strongest form: the function cannot leak what it is never given."""
    import inspect

    parameters = list(inspect.signature(configuration_failure_message).parameters)
    assert parameters == ["key", "detail"], (
        f"configuration_failure_message takes {parameters}; a `value` parameter "
        f"would put the credential one formatting mistake from a log line"
    )


# --- TR-026: content capture is off unless explicitly enabled ---------------


def test_content_capture_is_off_by_default() -> None:
    """The default *is* the requirement. Logs are the largest and least-reviewed
    sink in the system, and a toggle defaulting on would put prompt text in
    every operator's aggregator before anyone decided it should be there."""
    assert not content_capture_enabled({})


@pytest.mark.parametrize("value", ["0", "true", "TRUE", "yes", "on", "", " 1"])
def test_only_the_exact_value_enables_capture(value: str) -> None:
    """Fixed like the provider opt-in, and for the same reason: a control whose
    spelling is negotiable is one a check cannot assert the absence of."""
    assert not content_capture_enabled({CONTENT_CAPTURE_ENV_VAR: value})


def test_the_exact_value_enables_capture() -> None:
    assert content_capture_enabled(
        {CONTENT_CAPTURE_ENV_VAR: CONTENT_CAPTURE_ENABLED_VALUE}
    )


def test_capture_is_scoped_to_log_output_alone() -> None:
    """TR-066. The invocation record and the normalized gateway error carry no
    prompt or completion content *at any setting* of this toggle, because both
    field sets are closed and neither names one.

    Asserted against the record's field list, so the toggle cannot widen it
    even if someone wanted it to — the closure is what makes the scoping real
    rather than a convention.
    """
    from gateway.models import InvocationRecord

    fields = set(InvocationRecord.model_fields)
    assert not (fields & {"prompt", "completion", "content", "messages", "system"})
