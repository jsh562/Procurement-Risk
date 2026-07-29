"""A validator for exactly the JSON Schema subset this contract uses.

Written rather than taken from a library, and that is a deliberate trade with a
specific reason: `jsonschema` is declared by `src/model`, and
`tests/checks/test_dependency_isolation.py` forbids a modeling distribution from
appearing in the serving boundary's resolution. Adding it to `src/api` broke
that check. The options were to widen the shared-infrastructure allowlist — that
is, to weaken an architecture contract so a test could pass — or to write the
part of a validator this contract needs. Weakening the boundary to accommodate a
test is the wrong direction, so this is the other option.

**The subset is bounded and measured, not guessed.** `contracts/openapi.yaml`
uses `type`, `properties`, `required`, `additionalProperties`, `items`, `enum`,
`const`, `pattern`, `minimum`, `maximum`, `format`, `$ref`, and `oneOf` in two
places (both the nullable idiom `[{$ref: X}, {type: 'null'}]`). It uses no
`allOf`, no `anyOf`, no `not`, no conditional applicators, and no external
references. `test_the_checker_covers_every_construct_the_contract_uses` asserts
that, so this module fails loudly if the contract later grows a construct it
does not implement — the failure mode a hand-written checker actually has is
silently ignoring a keyword, and that is what that test exists to prevent.

`format` and `default` are deliberately **not** enforced. Both are annotations
rather than assertions in JSON Schema 2020-12: the two formats here (`date`,
`date-time`) are already pinned by the code that produces them, and `default`
describes what a *client* may assume when a member is absent, which says nothing
about a response that carries it.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["SUPPORTED_KEYWORDS", "ConformanceError", "validate"]

#: Every keyword this checker implements. Compared against the contract's own
#: keyword set by a test, so a construct added to the document without support
#: here fails rather than passing unchecked.
SUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        "$ref",
        "additionalProperties",
        "const",
        "default",
        "description",
        "enum",
        "example",
        "examples",
        "format",
        "items",
        "maxItems",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "oneOf",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
        "uniqueItems",
    }
)

_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


class ConformanceError(Exception):
    """One or more places where an instance departs from the contract."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        rendered = "\n".join(f"  {failure}" for failure in failures[:10])
        more = f"\n  … and {len(failures) - 10} more" if len(failures) > 10 else ""
        super().__init__(
            f"the response does not conform to contracts/openapi.yaml "
            f"({len(failures)} failure(s)):\n{rendered}{more}\n\n"
            "The contract is the authority: three later epics are told to build against it, so a "
            "disagreement is a question about which of the two is wrong, not a licence to relax "
            "the schema."
        )


def _resolve(schema: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """Follow a document-internal `$ref`. External references are unreachable
    here — the assertion below is what makes that a stated property."""
    ref = schema.get("$ref")
    if ref is None:
        return schema
    assert ref.startswith("#/"), f"only document-internal refs are supported, got {ref!r}"
    node: Any = document
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return _resolve(node, document)


def _check(instance: Any, schema: dict[str, Any], document: dict[str, Any], path: str) -> list[str]:
    schema = _resolve(schema, document)
    where = path or "<root>"
    failures: list[str] = []

    if "oneOf" in schema:
        # The nullable idiom. Exactly one branch must match, which is also what
        # distinguishes `null` from an object that happens to be empty.
        matches = [
            branch for branch in schema["oneOf"] if not _check(instance, branch, document, path)
        ]
        if len(matches) != 1:
            failures.append(
                f"{where}: matched {len(matches)} of {len(schema['oneOf'])} oneOf branches, "
                "expected exactly 1"
            )
        return failures

    # `type` may be a single name or a list of them — the contract uses the list
    # form for the nullable-integer idiom, `[integer, 'null']`, which is how
    # `PercentFigure.percent` says "an integer, or absent because the figure is
    # bounded". Normalised so both spellings take one path.
    declared = schema.get("type")
    if declared is not None:
        names = [declared] if isinstance(declared, str) else list(declared)

        if instance is None:
            if "null" not in names:
                failures.append(f"{where}: expected {'/'.join(names)}, got null")
            return failures

        concrete = [name for name in names if name != "null"]
        # `bool` is a subclass of `int` in Python; a boolean where an integer is
        # declared is a type error the contract means to catch.
        if isinstance(instance, bool) and "boolean" not in concrete:
            failures.append(f"{where}: expected {'/'.join(concrete)}, got boolean")
            return failures
        if not any(isinstance(instance, _TYPES[name]) for name in concrete):
            failures.append(
                f"{where}: expected {'/'.join(concrete)}, got {type(instance).__name__}"
            )
            return failures

    if "const" in schema and instance != schema["const"]:
        failures.append(f"{where}: expected the constant {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        failures.append(f"{where}: {instance!r} is not one of {schema['enum']!r}")
    if (
        "pattern" in schema
        and isinstance(instance, str)
        and not re.search(schema["pattern"], instance)
    ):
        failures.append(f"{where}: {instance!r} does not match {schema['pattern']!r}")
    if "minLength" in schema and isinstance(instance, str) and len(instance) < schema["minLength"]:
        failures.append(f"{where}: {instance!r} is shorter than minLength {schema['minLength']}")
    if "minimum" in schema and isinstance(instance, int | float) and instance < schema["minimum"]:
        failures.append(f"{where}: {instance} is below the minimum {schema['minimum']}")
    if "maximum" in schema and isinstance(instance, int | float) and instance > schema["maximum"]:
        failures.append(f"{where}: {instance} is above the maximum {schema['maximum']}")

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in instance:
                failures.append(f"{where}: required member {name!r} is absent")
        # The closure this whole module exists for.
        if schema.get("additionalProperties") is False:
            for name in instance:
                if name not in properties:
                    failures.append(f"{where}: {name!r} is not declared by the contract")
        for name, value in instance.items():
            if name in properties:
                failures.extend(
                    _check(value, properties[name], document, f"{path}/{name}" if path else name)
                )

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            failures.append(
                f"{where}: {len(instance)} items is below minItems {schema['minItems']}"
            )
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            failures.append(f"{where}: {len(instance)} items exceeds maxItems {schema['maxItems']}")
        if schema.get("uniqueItems") and len(instance) != len({repr(i) for i in instance}):
            failures.append(f"{where}: items are not unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(instance):
                failures.extend(_check(item, item_schema, document, f"{path}[{index}]"))

    return failures


def validate(instance: Any, schema_name: str, document: dict[str, Any]) -> None:
    """Validate ``instance`` against ``document``'s named component schema.

    Raises:
        ConformanceError: with every departure listed, not just the first — a
            validator that stopped at the first failure would turn one review
            into several.
    """
    schema = document["components"]["schemas"][schema_name]
    failures = _check(instance, schema, document, "")
    if failures:
        raise ConformanceError(failures)
