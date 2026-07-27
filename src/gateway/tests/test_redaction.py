"""TR-024 / TR-059 / TR-061: redaction across reprs, tracebacks, and the store.

The three cases here are the ones that leak in practice, and none of them is a
string someone decided to log.

**A `repr`** is written by whoever wrote the class — often the SDK — and appears
wherever a debugger, a logger, or an f-string touches the object.

**A traceback** renders *local frames* when variable capture is on, so a
credential that was only ever a local variable in the function that raised is in
the crash report.

**A committed fixture** is written once and read forever, by everyone with the
repository.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from gateway.config import CREDENTIAL_ENV_VAR
from gateway.provider import CredentialHandle
from gateway.redaction import (
    CREDENTIAL_PATTERN,
    REDACTED,
    SINKS,
    contains_credential_material,
    credential_findings,
    redact,
)

#: Key-shaped, and deliberately *not* a real key. Assembled from parts so this
#: file does not become a committed literal E001's supply-chain scan flags —
#: that scan cannot tell a fake from a real one, which is what makes it useful.
SHAPED = "sk-" + "ant-" + "A" * 20
CONFIGURED = "configured-value-not-key-shaped"
ENV = {CREDENTIAL_ENV_VAR: CONFIGURED}
SECRET = "secret-value-here"


# --- TR-060: the two detectors ----------------------------------------------


def test_the_shaped_detector_matches_without_a_configured_value() -> None:
    """Detector (b) is why a committed fixture can be scanned on a machine with
    no key. A scan able only to match a value it held would report every file
    clean on exactly the machine where scanning matters."""
    assert contains_credential_material(f"authorization: {SHAPED}", {})


def test_the_shaped_detector_needs_the_full_length() -> None:
    """Anchored at sixteen characters after the prefix, as TR-060 states. A
    looser pattern would match the bare prefix wherever it appears in prose."""
    assert not CREDENTIAL_PATTERN.search("sk-" + "ant-" + "short")


def test_the_exact_detector_matches_a_value_that_is_not_key_shaped() -> None:
    """Detector (a) exists because a configured value need not look like a key
    — a development placeholder, or a truncated key exported by hand. The
    pattern would not match either, and both are still the credential."""
    assert contains_credential_material(f"token={CONFIGURED}", ENV)
    assert not contains_credential_material(f"token={CONFIGURED}", {})


def test_both_detectors_are_reported() -> None:
    findings = credential_findings(f"{CONFIGURED} and {SHAPED}", ENV)
    assert CONFIGURED in findings
    assert SHAPED in findings


def test_ordinary_text_is_not_a_finding() -> None:
    """A detector that matched freely would be turned off within a week."""
    assert not contains_credential_material("no credential here at all", ENV)
    assert not contains_credential_material("", ENV)


# --- TR-024: redaction over the shapes a sink actually carries ---------------


def test_a_bare_string_is_redacted() -> None:
    assert redact(f"key={SHAPED}", {}) == f"key={REDACTED}"


def test_a_nested_payload_is_redacted() -> None:
    """A sink that redacted only top-level strings would pass every test written
    against a flat example and leak on the first structured one — and the spool
    payload and the log `extra` are both structured."""
    payload = {"headers": [{"authorization": SHAPED}], "note": "fine"}
    assert redact(payload, {}) == {
        "headers": [{"authorization": REDACTED}],
        "note": "fine",
    }


def test_a_mapping_key_is_redacted_too() -> None:
    """Unusual and not impossible: an environment snapshot inverted somewhere on
    its way to a log is exactly a dict keyed by its values."""
    assert redact({SHAPED: "value"}, {}) == {REDACTED: "value"}


def test_a_tuple_keeps_its_shape() -> None:
    assert redact((SHAPED, "ok"), {}) == (REDACTED, "ok")


def test_a_non_text_leaf_is_returned_unchanged() -> None:
    """Coercing it would put a `repr` in the sink the caller never asked to
    write — and a `repr` is precisely how a client handle leaks."""
    assert redact({"count": 3, "flag": True, "none": None}, {}) == {
        "count": 3,
        "flag": True,
        "none": None,
    }


def test_the_marker_is_distinguishable_from_an_empty_value() -> None:
    """Blanking would make "this was redacted" and "this was empty" the same
    observation, and only one of them means the redaction worked."""
    assert REDACTED
    assert redact("", {}) == ""


def test_the_configured_value_is_replaced_whole() -> None:
    """Order matters: the exact detector runs first, so a real key is replaced
    entire rather than having its tail rewritten by the pattern and its prefix
    left behind."""
    assert CONFIGURED not in redact(f"a {CONFIGURED} b", ENV)


# --- TR-061: the handle holds the value off every rendering path ------------


def test_the_handle_reveals_only_through_a_named_call() -> None:
    handle = CredentialHandle(SECRET)
    assert handle.reveal() == SECRET


RENDERERS: list[Callable[[Any], str]] = [
    repr,
    str,
    "{}".format,
    lambda handle: f"{handle}",
    lambda handle: f"{handle!r}",
    lambda handle: f"{handle:>10}",
]


@pytest.mark.parametrize("render", RENDERERS, ids=range(len(RENDERERS)))
def test_no_rendering_path_shows_the_value(render: Callable[[Any], str]) -> None:
    """`__repr__` and `__str__` both, plus the formatting paths that bypass
    them. Overriding one is the common half-measure — `repr` appears in
    tracebacks and debuggers, `str` in f-strings and log messages, and a value
    safe in one and not the other leaks through whichever the next author
    reaches for."""
    rendered = render(CredentialHandle(SECRET))
    assert SECRET not in rendered
    assert REDACTED in rendered


def test_the_handle_refuses_to_be_serialized() -> None:
    """Serialization is one of TR-059's sinks. A handle that pickled would put
    the value in whatever the pickle was written to — a cache, a queue, a crash
    dump."""
    import pickle

    with pytest.raises(TypeError, match="must not be serialized"):
        pickle.dumps(CredentialHandle(SECRET))


def test_the_handle_has_no_attribute_carrying_the_value_publicly() -> None:
    """TR-061 says the client handle must expose no attribute carrying the
    value. `__slots__` also means no `__dict__` for a debugger or a serializer
    to walk."""
    handle = CredentialHandle(SECRET)
    assert not hasattr(handle, "__dict__")
    public = sorted(name for name in dir(handle) if not name.startswith("_"))
    assert public == ["from_environment", "reveal"], public


def test_a_traceback_holding_the_handle_renders_no_value() -> None:
    """The sink TR-059 names explicitly: a variable-capturing traceback renders
    local frames, so a credential that was only ever a local is in the crash
    report. The handle is what makes the frame safe to render."""
    import traceback

    def boom() -> None:
        credential = CredentialHandle(SECRET)
        raise RuntimeError(f"failed with {credential}")

    rendered = ""
    try:
        boom()
    except RuntimeError as exc:
        rendered = "".join(traceback.format_exception(exc))

    assert rendered, "the traceback was not captured, so this asserts nothing"
    assert SECRET not in rendered
    assert REDACTED in rendered


def test_a_missing_credential_names_the_key_and_nothing_else() -> None:
    from gateway.errors import ProviderUnavailableError

    with pytest.raises(ProviderUnavailableError) as raised:
        CredentialHandle.from_environment({})
    assert CREDENTIAL_ENV_VAR in str(raised.value)


# --- TR-059: the inventory is closed at five --------------------------------


def test_the_sink_inventory_is_exactly_five() -> None:
    """Closure is what makes "every sink is covered" decidable against a list
    rather than against reviewer judgement. An inventory that grew silently
    would have no denominator."""
    assert len(SINKS) == 5, SINKS
    assert set(SINKS) == {
        "log_output",
        "exception_payload",
        "fixture_store",
        "invocation_spool",
        "check_output",
    }
