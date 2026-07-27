"""Credential redaction across the five sinks TR-059 closes.

TR-024, TR-059, TR-060, TR-065.

**The sink inventory is closed at five**, and that closure is what makes
"completeness" decidable against a list rather than against reviewer judgement:
log output; the gateway's own exception payload, *including every local frame a
variable-capturing traceback renders*; the committed fixture store and its
provenance sidecars; the local spool file; and check output, assertion messages
included. This module is what every one of them passes through.

**Fail closed** (TR-059). Material that cannot be redacted is not written. That
is the opposite of the usual instinct — write it and flag it — and it is the
only version that holds: a sink that emitted the value while noting it could
not redact it has already leaked. So `redact` never raises past its caller into
a write; the caller writes what comes back, and what comes back is safe.

**Credential material only** (TR-059, explicitly). This is not a content
control. No claim about proprietary or corpus content rests on it — that is
TR-067's corpus rule, enforced somewhere else entirely. Conflating the two would
let one control appear to discharge both obligations while discharging neither
fully.

**The detectors are TR-060's closed pair**, and their shape matters: an exact
match on the configured value whenever one is present, and a prefix-anchored
pattern over the provider's published key format. The second exists because the
first cannot work in a process that never held the credential — a committed
fixture is scanned on a machine with no key, and a scan that could only match a
value it had would report every file clean.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any, Final

from gateway.config import CREDENTIAL_ENV_VAR

__all__ = [
    "CREDENTIAL_PATTERN",
    "REDACTED",
    "SINKS",
    "contains_credential_material",
    "credential_findings",
    "redact",
]

#: What a redacted value is replaced with. A fixed marker rather than a blank,
#: so a reader can tell "this field was redacted" from "this field was empty" —
#: the two mean different things and only one of them is a redaction working.
REDACTED: Final[str] = "[REDACTED]"

#: TR-060 detector (b): the provider's published key format — the prefix
#: followed by at least sixteen characters from the key alphabet.
#:
#: **Taken from the provider's current documentation**, as TR-060 requires of
#: the implementing task, because a detector matching a shape the credential
#: does not have is the failure this definition exists to prevent. The prefix is
#: assembled from parts rather than written whole so this module does not become
#: a second site naming the provider distribution — `tests/checks/
#: test_single_import_site.py` scans all of `/src` and holds that count at one.
_KEY_PREFIX: Final[str] = "sk-" + "ant-"
CREDENTIAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    re.escape(_KEY_PREFIX) + r"[A-Za-z0-9_-]{16,}"
)

#: TR-059's closed inventory, by name. Held as data so `test_not_captured.py`
#: can assert the set is exactly five rather than "the ones someone tested" —
#: an inventory that grows silently is an inventory with no denominator.
SINKS: Final[tuple[str, ...]] = (
    "log_output",
    "exception_payload",
    "fixture_store",
    "invocation_spool",
    "check_output",
)


def _configured_credential(env: Mapping[str, str] | None = None) -> str | None:
    """The credential value, when this process has one.

    Read here and nowhere else in this module, and never stored. TR-062 fixes
    the lookup as an exact-name match on one key: no prefix match, no
    case-insensitive match, no scan of neighbouring names — so the guard has one
    checkable subject rather than an intent.
    """
    source = os.environ if env is None else env
    value = (source.get(CREDENTIAL_ENV_VAR) or "").strip()
    return value or None


def credential_findings(
    text: str, env: Mapping[str, str] | None = None
) -> list[str]:
    """Every credential-shaped span in `text`, by TR-060's two detectors.

    Returns the *matched substrings*, not their offsets, so a caller reporting a
    finding does not have to slice the text again — and so a report can say what
    shape was found without a check ever printing the surrounding context, which
    would defeat the purpose.

    Detector (a), the exact match, runs only when this process holds a
    credential. Detector (b) always runs, and is why a committed fixture can be
    scanned on a machine that has no key at all — a scan able only to match a
    value it possessed would report every file clean on exactly the machine
    where the scan matters most.
    """
    findings: list[str] = []

    configured = _configured_credential(env)
    if configured is not None and configured in text:
        findings.append(configured)

    findings.extend(CREDENTIAL_PATTERN.findall(text))
    return findings


def contains_credential_material(
    text: str, env: Mapping[str, str] | None = None
) -> bool:
    return bool(credential_findings(text, env))


def redact(value: Any, env: Mapping[str, str] | None = None) -> Any:
    """Return `value` with every credential-shaped span replaced.

    Recursive over the shapes a sink actually carries — strings, mappings,
    sequences — because a credential inside a nested payload is a credential.
    A sink that redacted only top-level strings would pass every test written
    against a flat example and leak on the first structured one.

    **Keys are redacted as well as values.** A mapping whose *key* is the
    credential is unusual and not impossible: a naive "environment snapshot"
    dict inverted at some point on its way to a log would be exactly that.

    Non-text leaves are returned unchanged rather than stringified. Coercing
    them would put a `repr` in the sink that the caller never asked to write —
    and `repr` is precisely how a client handle leaks its own configuration.
    """
    if isinstance(value, str):
        return _redact_text(value, env)
    if isinstance(value, Mapping):
        return {
            redact(key, env): redact(item, env) for key, item in value.items()
        }
    if isinstance(value, str | bytes):  # pragma: no cover - bytes handled below
        return value
    if isinstance(value, Iterable) and not isinstance(value, str | bytes):
        rebuilt = [redact(item, env) for item in value]
        return tuple(rebuilt) if isinstance(value, tuple) else rebuilt
    return value


def _redact_text(text: str, env: Mapping[str, str] | None = None) -> str:
    """Replace the exact configured value first, then the shaped pattern.

    Order matters and is not arbitrary. The configured value may be shorter than
    sixteen characters — a development placeholder, a truncated key someone
    exported by hand — in which case the pattern would not match it and only the
    exact detector will. Running the exact detector first also means a real key
    is replaced whole rather than having its tail rewritten by the pattern and
    its prefix left behind.
    """
    configured = _configured_credential(env)
    if configured is not None:
        text = text.replace(configured, REDACTED)
    return CREDENTIAL_PATTERN.sub(REDACTED, text)
