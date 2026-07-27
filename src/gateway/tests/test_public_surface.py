"""TR-002 / OBJ1 VC3: no provider SDK type reaches the gateway's public surface.

The half the import contracts cannot carry. A contract constrains *imports* and
reports *module paths*; this constrains *types* and reports them **by name**,
which is what OBJ1 VC3 asks for. The gap between the two is real and was
demonstrated once already in this repository: re-export laundering passes an
import contract, because the module that re-exports a foreign object need never
import the package that defines it.

The check is written as an allowlist over *roots* rather than a denylist of
provider packages, for two reasons.

It is stronger. A denylist catches the SDK this epic happens to use; an
allowlist catches any third-party type — a future provider, an HTTP client's
response object, a retry library's exception — all of which would couple
consumers exactly as much.

And it is the only formulation available here. `tests/checks/
test_single_import_site.py` scans **all** of `/src`, tests included, and
asserts exactly one file in the repository names the provider distribution.
A denylist would have to spell it out, and would make this the second.
"""

from __future__ import annotations

import sys
import typing
from collections.abc import Iterator
from types import UnionType
from typing import Any, get_args, get_origin

import pytest
from pydantic import BaseModel

from gateway import api

#: Roots a public-surface type may come from. `gateway` because the surface is
#: gateway-owned by definition; `pydantic` because the request and result types
#: are models and their base class is unavoidably visible in the MRO. Every
#: standard-library root is admitted wholesale via `sys.stdlib_module_names` —
#: `str`, `int` and `None` are not a coupling anyone needs protecting from.
ALLOWED_ROOTS: frozenset[str] = frozenset({"gateway", "pydantic"}) | frozenset(
    sys.stdlib_module_names
)


def _root_of(obj: Any) -> str | None:
    """The distribution a type came from, or None if it has no module.

    `None` covers the shapes that are not types at all — `Ellipsis`, a literal
    value inside an `Annotated`, a `TypeVar` — none of which can leak a
    provider class into a signature.
    """
    module = getattr(obj, "__module__", None)
    if not isinstance(module, str) or not module:
        return None
    return module.split(".")[0]


def _name_of(obj: Any) -> str:
    """How a leaked type is reported. Fully qualified deliberately: `Client` on
    its own tells a reader nothing about where it came from, and where it came
    from is the entire finding."""
    module = getattr(obj, "__module__", "<unknown>")
    qualname = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", repr(obj))
    return f"{module}.{qualname}"


def _walk(annotation: Any, seen: set[int] | None = None) -> Iterator[Any]:
    """Every type mentioned anywhere inside an annotation.

    Recursive because the leak this exists to catch does not have to be the
    whole annotation. `list[ProviderResponse]`, `dict[str, ProviderResponse]`
    and `ProviderResponse | None` each couple a consumer to the provider just
    as firmly as a bare one, and a check that only looked at the outermost type
    would pass all three.
    """
    if seen is None:
        seen = set()
    if id(annotation) in seen:
        return
    seen.add(id(annotation))

    # PEP 695 aliases (`type Outcome = str`) are transparent to a caller, so
    # they must be transparent here too — otherwise an alias would be somewhere
    # to hide a leaked type behind a gateway-owned name.
    value = getattr(annotation, "__value__", None)
    if value is not None and isinstance(annotation, typing.TypeAliasType):
        yield from _walk(value, seen)
        return

    args = get_args(annotation)
    if args:
        origin = get_origin(annotation)
        # `UnionType` and `typing.Union` are the union machinery itself, not a
        # type anyone could receive; the members are what matter.
        if origin is not None and origin is not UnionType and origin is not typing.Union:
            yield origin
        for arg in args:
            yield from _walk(arg, seen)
        return

    yield annotation


def _annotations_of(obj: Any) -> dict[str, Any]:
    """Resolved annotations for a callable or a model.

    Resolved rather than raw: every module here carries
    `from __future__ import annotations`, so the raw values are strings, and a
    string comparison would silently pass on everything.
    """
    if isinstance(obj, type) and issubclass(obj, BaseModel):
        return {name: field.annotation for name, field in obj.model_fields.items()}
    if callable(obj):
        return dict(typing.get_type_hints(obj))
    return {}


def _public_names() -> list[str]:
    """What `__all__` declares, which is the surface a consumer reads."""
    return sorted(api.__all__)


def test_the_module_declares_its_surface() -> None:
    """Everything below reads `__all__`. If it were empty or missing, every
    other test in this file would pass by having nothing to check — the failure
    mode worth ruling out first."""
    assert _public_names(), "gateway.api declares no __all__; the surface is unenumerable"


@pytest.mark.parametrize("name", _public_names())
def test_the_exported_object_is_gateway_owned(name: str) -> None:
    """The object itself, before its annotations.

    A re-exported provider class has no annotations to inspect and would sail
    past the signature check below — it *is* the leak, rather than appearing in
    one.
    """
    obj = getattr(api, name)
    root = _root_of(obj)
    if root is None:
        return
    assert root in ALLOWED_ROOTS, (
        f"gateway.api exports {name!r}, which is {_name_of(obj)} — a type this "
        f"package does not own. A consumer using the public surface would need "
        f"{root!r} installed to name it (TR-002)."
    )


@pytest.mark.parametrize("name", _public_names())
def test_no_foreign_type_appears_in_a_public_signature(name: str) -> None:
    """OBJ1 VC3's substance: the leaked type, reported by name."""
    obj = getattr(api, name)
    leaks = [
        (field, mentioned)
        for field, annotation in _annotations_of(obj).items()
        for mentioned in _walk(annotation)
        if (root := _root_of(mentioned)) is not None and root not in ALLOWED_ROOTS
    ]
    assert not leaks, "\n".join(
        [f"gateway.api.{name} exposes types this package does not own (TR-002):"]
        + [f"  {field}: {_name_of(mentioned)}" for field, mentioned in leaks]
    )


#: A stand-in for a third-party type, used only by the self-test below. Named
#: for something that does not exist rather than for the real provider, because
#: `tests/checks/test_single_import_site.py` scans this file too.
_FOREIGN_MODULE = "some_provider_sdk.types"


class _Foreign:
    """Not a real provider type — a type this package does not own is all the
    self-test needs, and inventing one keeps the fixture honest."""


_Foreign.__module__ = _FOREIGN_MODULE


def test_the_walker_sees_a_type_nested_inside_a_container() -> None:
    """A test of the check, not of the surface.

    Without it, `_walk` could regress to yielding only the outermost type and
    every assertion above would still pass — a leak inside `list[...]` would go
    unreported and the suite would stay green, which is precisely the "coverage
    is a claim rather than a measurement" failure the checks exist to prevent.
    """

    found = {_name_of(t) for t in _walk(dict[str, list[_Foreign | None]])}
    reported = [name for name in found if name.startswith(f"{_FOREIGN_MODULE}.")]
    assert reported, (
        f"the annotation walker missed a type nested two containers deep: {sorted(found)}"
    )
    assert reported[0].endswith("_Foreign"), (
        f"the walker found the type but reported it unusably: {reported[0]}"
    )
