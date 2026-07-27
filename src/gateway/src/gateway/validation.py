"""Schema submission, post-decode validation, and the single bounded repair.

TR-005 through TR-008. Three obligations that only work together:

**Submit natively** (TR-005). The provider has a structured-output mode that
constrains decoding, and using it is strictly better than asking for JSON in a
prompt — a constraint the decoder enforces cannot be violated, where a
constraint in a prompt is a suggestion.

**Enforce what the mode drops** (TR-005, second clause). The native mode
accepts a *subset* of JSON Schema. Everything outside that subset is not
rejected — it is folded into the schema's `description` as prose, where the
model *might* follow it. A caller who wrote `minimum: 1` and received `0` was
not told their constraint had been demoted to a hint. So every output is
validated against the caller's schema in full after decoding, and
`residual_constraints` names exactly which of the caller's constraints made
that post-decode step load-bearing rather than ceremonial.

**Never return an unvalidated value** (TR-006), with **at most one repair**
(TR-007) and a **fail-closed second failure** (TR-008).

This module holds no provider import and no transport. It is handed a decoded
payload and a callable that can produce another one, which is what lets the
repair budget be tested exhaustively without a credential, a network, or a
fixture — and what keeps the transport budget of TR-010 cleanly separate from
the repair budget, since neither module can see the other's counter.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from gateway.errors import GatewayValidationError

__all__ = [
    "MAX_REPAIR_ATTEMPTS",
    "ResidualConstraint",
    "ValidationFailure",
    "residual_constraints",
    "validate_or_repair",
]

#: TR-007. One, and the number is named rather than written as a literal in the
#: loop below, because "at most one repair" is the requirement and a bare `1`
#: in a comparison is not greppable against it.
MAX_REPAIR_ATTEMPTS: Final[int] = 1

#: Keywords the native mode carries through unchanged. Derived from the
#: provider's own schema transform rather than transcribed from documentation:
#: the transform is what actually runs, and a hand-kept list would be a second
#: statement of the same fact with nothing comparing the two.
#:
#: These are the *structural* keys the residual walk recurses into rather than
#: reports. A constraint keyword is anything else.
_STRUCTURAL_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"$defs", "$ref", "properties", "items", "anyOf", "oneOf", "allOf", "type"}
)

#: Reported as residual even though the native schema retains a key of the same
#: name, because retention is not preservation.
#:
#: `oneOf` means *exactly one* variant matches. The transform rewrites it to
#: `anyOf`, which means *at least one* — a strictly weaker constraint wearing a
#: near-identical spelling. A walk comparing key presence would see `oneOf`
#: gone and `anyOf` present and call it a rename.
_WEAKENED_KEYWORDS: Final[frozenset[str]] = frozenset({"oneOf"})


@dataclass(frozen=True)
class ResidualConstraint:
    """One constraint the native mode could not express.

    Named by JSON Pointer rather than by dotted path: a property literally
    called `a.b` is legal JSON Schema, and a dotted path cannot distinguish it
    from a nested one.
    """

    pointer: str
    keyword: str
    value: Any

    def __str__(self) -> str:
        return f"{self.pointer or '/'}: {self.keyword}={self.value!r}"


@dataclass(frozen=True)
class ValidationFailure:
    """One reason a decoded payload did not satisfy the caller's schema.

    `field_path` and `message` are exactly what TR-007 requires the repair to
    carry. Kept as a pair rather than a formatted string so the record can
    store the paths without re-parsing prose.
    """

    field_path: str
    message: str

    def __str__(self) -> str:
        return f"{self.field_path or '<root>'}: {self.message}"


def _walk(original: Any, transformed: Any, pointer: str) -> list[ResidualConstraint]:
    """Compare one node of the caller's schema against its transformed form."""
    if not isinstance(original, Mapping):
        return []
    transformed_map: Mapping[str, Any] = transformed if isinstance(transformed, Mapping) else {}

    residuals: list[ResidualConstraint] = []

    for keyword, value in original.items():
        if keyword in _WEAKENED_KEYWORDS:
            residuals.append(ResidualConstraint(pointer, keyword, value))
            continue
        if keyword in _STRUCTURAL_KEYWORDS:
            continue
        if keyword == "additionalProperties":
            # The transform forces this to False regardless of what the caller
            # asked for. False is *stricter* than True, so a caller who allowed
            # extra properties and got none has no constraint violated — and a
            # caller who forbade them got what they asked for. Neither is a
            # residual, and reporting it would train readers to ignore the list.
            continue
        if keyword not in transformed_map or transformed_map[keyword] != value:
            residuals.append(ResidualConstraint(pointer, keyword, value))

    for container in ("properties", "$defs"):
        for name, sub in (original.get(container) or {}).items():
            counterpart = (transformed_map.get(container) or {}).get(name)
            residuals.extend(_walk(sub, counterpart, f"{pointer}/{container}/{name}"))

    if "items" in original:
        residuals.extend(_walk(original["items"], transformed_map.get("items"), f"{pointer}/items"))

    for container in ("anyOf", "oneOf", "allOf"):
        variants = original.get(container)
        if not isinstance(variants, Sequence) or isinstance(variants, str | bytes):
            continue
        # `oneOf` is rewritten to `anyOf`, so the transformed side is looked up
        # under the name it actually landed on rather than the one it left.
        landed = "anyOf" if container == "oneOf" else container
        transformed_variants = transformed_map.get(landed) or []
        for index, variant in enumerate(variants):
            counterpart = transformed_variants[index] if index < len(transformed_variants) else None
            residuals.extend(_walk(variant, counterpart, f"{pointer}/{container}/{index}"))

    return residuals


def residual_constraints(
    original: Mapping[str, Any], transformed: Mapping[str, Any]
) -> tuple[ResidualConstraint, ...]:
    """The caller's constraints the native mode could not carry.

    Args:
        original: The caller's JSON Schema, as they wrote it.
        transformed: The same schema after the provider's transform — what the
            decoder is actually constrained by.

    Returns:
        Every constraint present in the first and absent, altered, or weakened
        in the second, deepest-last in document order.

    This is disclosure, not enforcement. Enforcement is `validate_or_repair`
    below, which validates against the caller's schema in full and therefore
    catches these whether or not anyone reads this list. What the list buys is
    an answer to "why did this invocation need a repair?" that does not require
    diffing two schemas by hand — and a way for a caller to learn that a
    constraint they wrote is being enforced by retry rather than by decoding,
    which is the same result at a different price.
    """
    return tuple(_walk(original, transformed, ""))


def _decode(payload: str) -> tuple[Any, ValidationFailure | None]:
    try:
        return json.loads(payload), None
    except json.JSONDecodeError as exc:
        # The decode failure is a validation failure, not a separate category.
        # TR-006 forbids returning an unvalidated value, and text that is not
        # JSON is the most unvalidated a value gets. Giving it its own error
        # type would let a caller handle "malformed" differently from "invalid"
        # when there is nothing different to do about it.
        return None, ValidationFailure("<root>", f"output is not valid JSON: {exc.msg}")


def _validate[ModelT: BaseModel](
    schema: type[ModelT], payload: str
) -> tuple[ModelT | None, tuple[ValidationFailure, ...]]:
    decoded, decode_failure = _decode(payload)
    if decode_failure is not None:
        return None, (decode_failure,)
    try:
        return schema.model_validate(decoded), ()
    except ValidationError as exc:
        return None, tuple(
            ValidationFailure(
                field_path="/".join(str(part) for part in error["loc"]),
                message=error["msg"],
            )
            for error in exc.errors()
        )


def repair_instruction(failures: Sequence[ValidationFailure]) -> str:
    """What the repair attempt carries back to the model (TR-007).

    The failing field path *and* the validation message, both required by the
    requirement and both load-bearing: the path without the message says
    something is wrong here without saying what, and the message without the
    path says what is wrong without saying where. Either alone leaves the model
    guessing at the half it was not told.

    Contains no prompt or completion content — only paths and validator
    messages — so it is safe against TR-026 wherever it is logged or recorded.
    """
    lines = "\n".join(f"- {failure}" for failure in failures)
    return (
        "The previous response did not satisfy the required schema. "
        "Correct these and return the corrected value only:\n"
        f"{lines}"
    )


def validate_or_repair[ModelT: BaseModel](
    schema: type[ModelT],
    payload: str,
    repair: Callable[[str], str],
) -> tuple[ModelT, int]:
    """Validate, repair at most once, then fail closed.

    TR-006, TR-007 and TR-008 in one function, because they are one rule split
    across three statements and implementing them separately is how the three
    drift apart.

    Args:
        schema: The caller's schema. A pydantic model rather than a raw JSON
            Schema mapping — see the module docstring of `orchestrator.py` and
            plan AD-010 for why. `model_validate` then enforces the caller's
            constraints in full, including every one the native mode demoted to
            a description hint.
        payload: The model's first response, undecoded.
        repair: Issues the repair request and returns the second response. Takes
            the instruction and returns raw text, so this module needs no
            provider, no transport and no deadline — the caller supplies the
            one effect and keeps its own budget.

    Returns:
        The validated value and the number of repair attempts consumed, which
        is 0 or 1 and is what TR-078 maps onto `valid` and `repaired`.

    Raises:
        GatewayValidationError: The second response failed too. The repair
            budget is one attempt, so there is no third — TR-008 fails closed
            here rather than trying again with a longer message.
    """
    validated, failures = _validate(schema, payload)
    if validated is not None:
        return validated, 0

    for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
        validated, failures = _validate(schema, repair(repair_instruction(failures)))
        if validated is not None:
            return validated, attempt

    raise GatewayValidationError(
        f"the model produced no schema-valid value after {MAX_REPAIR_ATTEMPTS} repair "
        f"attempt(s); failing fields: {', '.join(f.field_path for f in failures)}",
        field_paths=tuple(failure.field_path for failure in failures),
        repair_attempt_count=MAX_REPAIR_ATTEMPTS,
    )
