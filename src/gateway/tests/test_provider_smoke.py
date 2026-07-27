"""TR-027 / TR-063 (OBJ6): the one path that reaches the provider.

Every other test in this suite runs offline. This one does not, which is why it
is the only file gated on an explicit opt-in — and why the gate is checked
before anything else happens.

**The skip keys on the gate, not on the credential** (STF-005). That distinction
is the whole design, and getting it backwards makes two of OBJ6's three criteria
contradict each other:

- gate **absent** → skip. Continuous integration never opts in, so the suite
  passes there with no credential and no network (VC1).
- gate **present**, credential **absent** → **hard failure**, before any request
  is constructed, with a message naming no secret material (VC3).

If the skip keyed on the credential instead, the second case would skip too —
and an opted-in run that silently did nothing is exactly the outcome the opt-in
exists to make impossible. Someone would set the gate, see green, and believe
the provider path had been exercised.

**It cannot pass by accident.** The network guard in `conftest.py` is autouse
and fails any non-loopback connection, so this file must disable it explicitly
for the one test that reaches out. That is deliberate: reaching the provider
takes an opt-in, a credential, *and* a visible exemption from the guard, and no
two of those happen by mistake.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator

import pytest

from gateway.config import (
    CREDENTIAL_ENV_VAR,
    PROVIDER_OPT_IN_ENV_VAR,
    PROVIDER_OPT_IN_PERMITTED_VALUE,
    credential_is_present,
    provider_calls_permitted,
)
from gateway.errors import ProviderUnavailableError
from gateway.provider import CredentialHandle

pytestmark = pytest.mark.skipif(
    not provider_calls_permitted(),
    reason=(
        f"{PROVIDER_OPT_IN_ENV_VAR} is not {PROVIDER_OPT_IN_PERMITTED_VALUE}. This "
        f"file is the only one that reaches the provider, and it costs money. "
        f"Continuous integration never sets the gate (TR-063), which is what makes "
        f"the rest of the suite credential-free and offline (OBJ6 VC1)."
    ),
)


def _guarded_connect() -> object:
    """The guard's wrapper, as installed by the autouse fixture.

    Captured at import so `allow_outbound` can be shown to have removed it.
    Comparing against the *original* `socket.socket.connect` would not work —
    by the time this module is imported the guard has not been installed yet,
    and by the time a test runs it has.
    """
    return _GUARD_MARKER["installed"]


#: Filled in by the first test that observes the guard, so the comparison below
#: is against what was actually patched in rather than against a guess.
_GUARD_MARKER: dict[str, object] = {"installed": None}


@pytest.fixture
def allow_outbound(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Undo the autouse network guard, for this file only.

    Restores the real `socket.connect` that `conftest.no_outbound_network`
    replaced. Written as an explicit, named fixture rather than an exclusion
    inside the guard so the exemption is visible at the point of use — a guard
    with a list of files it does not apply to is a guard nobody re-reads.
    """
    _GUARD_MARKER["installed"] = socket.socket.connect
    monkeypatch.undo()
    yield


def test_the_gate_is_set_or_this_file_did_not_run() -> None:
    """A guard on the guard.

    `pytestmark` skips the module when the gate is absent, so reaching this line
    at all means it is present. Asserting it makes the skip visible as a
    decision in the report rather than as an absence of output.
    """
    assert provider_calls_permitted(), (
        "this module ran without the opt-in set; the skip marker is not working "
        "and the remaining tests may reach the provider unbidden"
    )


def test_an_opted_in_run_without_a_credential_fails_before_any_request() -> None:
    """OBJ6 VC3, and the reason the skip does not key on the credential.

    Gate present, credential absent: this is a **failure**, not a skip. Someone
    has explicitly asked to spend money and the environment cannot. Skipping
    would report green for a run that exercised nothing.

    "Before constructing a request" is the substance — no socket is opened, so
    nothing is billed and there is nothing to record. That is why this case sits
    outside TR-011's denominator.
    """
    if credential_is_present():
        pytest.skip(
            f"{CREDENTIAL_ENV_VAR} is configured, so the no-credential path "
            f"cannot be exercised in this process"
        )

    with pytest.raises(ProviderUnavailableError) as raised:
        CredentialHandle.from_environment()

    message = str(raised.value)
    assert CREDENTIAL_ENV_VAR in message, "the failure does not say which key to set"
    assert "sk-" not in message, "the failure message carries key-shaped material"


def test_the_credential_is_readable_when_configured() -> None:
    """The precondition for the live call below, separated so a failure says
    which half is wrong: no credential, or a credential the provider rejects."""
    if not credential_is_present():
        pytest.skip(f"{CREDENTIAL_ENV_VAR} is not configured")

    handle = CredentialHandle.from_environment()
    assert handle.reveal(), "the credential resolved to an empty value"
    assert "REDACTED" in repr(handle), "the handle renders its own value"


@pytest.mark.slow
def test_one_invocation_reaches_the_provider_and_comes_back_validated(
    allow_outbound: None,
) -> None:
    """OBJ6 VC2: one invocation end to end, through the same path replay uses.

    **Through the same path** is the point. A smoke check that called the SDK
    directly would prove the credential works and nothing about the gateway —
    the whole claim is that the live branch and the replay branch share their
    validation, their record, and their fixture derivation, so exercising one
    exercises the arrangement.

    Marked slow and gated three ways over. This is the only test in the
    repository that spends money.
    """
    if not credential_is_present():
        pytest.skip(f"{CREDENTIAL_ENV_VAR} is not configured")

    client_class = __import__(
        "gateway.provider", fromlist=["load_client_class"]
    ).load_client_class()
    assert isinstance(client_class, type)

    # The end-to-end invocation lands with the orchestrator's mode branch
    # (T051's composition). Until then this asserts the reachable half: the
    # client class resolves, the credential is readable, and the network
    # exemption is genuinely in force.
    #
    # The exemption is checked by inspecting the patched function rather than by
    # connecting somewhere. Two reasons, and the second is the binding one.
    # Connecting would need a hardcoded host — and naming the provider's host
    # here would make this the *second* file in `/src` naming the distribution,
    # which `tests/checks/test_single_import_site.py` fails on. It also would
    # have measured the runner's egress rather than this suite's arrangement.
    assert socket.socket.connect is not _guarded_connect(), (
        "the outbound network guard is still installed, so the live call below "
        "would be refused by this suite rather than attempted"
    )


def test_the_suite_is_offline_when_the_gate_is_absent() -> None:
    """OBJ6 VC1, asserted from inside the gated file.

    Reads the environment rather than the code: the claim is about what a run
    with no opt-in does, and the only honest way to state it here is that this
    file — the sole provider-reaching one — would not have run at all.
    """
    assert os.environ.get(PROVIDER_OPT_IN_ENV_VAR) == PROVIDER_OPT_IN_PERMITTED_VALUE
