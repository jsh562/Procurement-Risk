"""Canonical serialization, the closed hashed set, and the derived digests.

TR-019, TR-020, TR-038. Behind the computation boundary with `pricing.py` and
`timing.py`, so `gateway.provider` cannot reach it (TR-032).

**What the fixture key has to be true of.** Two requests that would produce the
same provider call must hash the same, or replay misses on a request it holds a
fixture for. Two requests that would produce a different call must hash
differently, or replay serves a fixture recorded for something else — which is
the failure that looks like everything working. Canonical serialization buys the
first; the closed field set buys the second.

**The closure rule runs the other way round from a list** (TR-020). "Every
sampling parameter" is a category, so enumerating parameters would go stale the
first time one was added. Instead: the hashed set **is** every field the
gateway's own request model declares, and a provider parameter the model does
not declare is not passed through — `extra="forbid"` makes it fail at request
construction rather than be hashed or dropped. Adding a parameter therefore
means adding a field, which a check can see.

That inversion is what makes the dangerous case impossible rather than merely
unlikely: a request field that reached the provider without reaching the hash
would give two genuinely different calls one fixture key, and the fixture store
would answer both with whichever was recorded first.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Final

from pydantic import BaseModel

__all__ = [
    "FIXTURE_KEY_PATTERN",
    "HASH_ALGORITHM",
    "canonical_json",
    "digest",
    "fixture_key",
    "prompt_template_version",
    "repair_fixture_key",
    "schema_version",
]

#: Named in the stored value rather than assumed. `llm_invocation.fixture_key`
#: is `CHECK`-constrained to `sha256:<64 hex>`, so a future algorithm change is
#: a visible schema change instead of a silently different digest under the same
#: column — and a stored key says which algorithm produced it without anyone
#: consulting this file.
HASH_ALGORITHM: Final[str] = "sha256"

FIXTURE_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Pinned rather than left to `json.dumps`'s defaults, which insert a space
#: after each separator. A formatting default that changed in a future Python
#: would change every fixture key in the store at once, and every replay would
#: miss for a reason nothing in this repository had altered.
_SEPARATORS: Final[tuple[str, str]] = (",", ":")


def canonical_json(value: Any) -> str:
    """One serialization per value, whatever order it was built in.

    Three choices, each of which has a wrong version that looks fine:

    **Sorted keys.** Two dictionaries holding the same items in a different
    insertion order are the same request. Preserving insertion order would give
    them different keys, so a replay would miss on a request it has a fixture
    for — and nothing in the data would show why.

    **No incidental whitespace**, with the separators stated here rather than
    inherited.

    **Not ASCII-escaped.** `ensure_ascii=True` would encode the same text
    differently depending on nothing the caller controls, and the corpus this
    project runs on is not guaranteed ASCII.

    Raises:
        TypeError: The value has no canonical form. Deliberately not handled by
            falling back to `repr` or `str`: two distinct objects would then
            share a serialization, and TR-020 requires a failure rather than a
            silent one.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=_SEPARATORS,
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(text: str) -> str:
    """A labelled hex digest of `text`."""
    return f"{HASH_ALGORITHM}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def schema_version(schema: type[BaseModel]) -> str:
    """A digest over the full schema definition, validators included (TR-038).

    **Over the JSON Schema rather than the field names**, and that is the whole
    point. Two models with identical field names and types can differ only in
    their *constraints* — a bound widened, a pattern relaxed — and a digest that
    could not tell them apart would keep replaying fixtures recorded before the
    edit. `model_json_schema()` carries the constraints, so a validator change
    moves the digest.

    Never a caller-declared string (TR-038, explicitly). A declared version is a
    promise nobody checks: it stays the same while the schema changes, and every
    stale fixture keeps matching.
    """
    return digest(canonical_json(schema.model_json_schema()))


def prompt_template_version(resolved_text: str) -> str:
    """A digest over the **resolved** template text (TR-038).

    Resolved rather than the template source, because two templates that resolve
    to the same text produce the same provider call and must share a key —
    keying on the source would split one call across two fixtures.
    """
    return digest(resolved_text)


def fixture_key(
    request: BaseModel,
    *,
    schema: type[BaseModel] | None = None,
    template: str | None = None,
) -> str:
    """The key a fixture is stored and looked up under (TR-019).

    Args:
        request: The gateway's own request model. **Its declared fields are the
            hashed set** — the closure of TR-020 is this parameter's type, not a
            list kept elsewhere.
        schema: The caller's output schema, whose digest is a hashed input
            (TR-038). A schema change must change the key, or a validator edit
            leaves every earlier fixture matching.
        template: The resolved prompt template text, digested for the same
            reason.

    Both are optional in the signature and neither is optional in effect: an
    invocation that submits a schema and passes none here would key on a
    strictly smaller input set. They are keyword-only so a caller cannot supply
    one positionally by accident, and the two are folded in under fixed names so
    a key computed with them can never collide with one computed without.
    """
    payload: dict[str, Any] = {
        "request": json.loads(request.model_dump_json()),
        "schema_version": schema_version(schema) if schema is not None else None,
        "prompt_template_version": (
            prompt_template_version(template) if template is not None else None
        ),
    }
    return digest(canonical_json(payload))


def repair_fixture_key(
    request: BaseModel,
    instruction: str,
    *,
    schema: type[BaseModel] | None = None,
    template: str | None = None,
) -> str:
    """The key the *repair* response is stored under (TR-007, TR-019).

    A repair is a second provider call, so it is a second fixture. Keying it on
    the original request **plus the repair instruction** is what makes a
    recorded repair replay as a repair rather than as a miss: the instruction
    carries the failing field paths and validator messages, so two invocations
    that failed the same way resolve the same repair, and two that failed
    differently do not share one.

    Folded in under a fixed marker so a repair key can never collide with the
    original request's key — they are different calls and must not answer each
    other. Without the marker, a request whose prompt happened to equal an
    instruction would resolve the wrong one.
    """
    payload: dict[str, Any] = {
        "kind": "repair",
        "original": fixture_key(request, schema=schema, template=template),
        "instruction": instruction,
    }
    return digest(canonical_json(payload))
