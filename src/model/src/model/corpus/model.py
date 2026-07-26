"""The pre-render document model and the digest taken over it.

FR-021 / DM-1…DM-6. Every determinism claim this epic makes reduces to
`document_model_hash`: the reproducibility comparison is against this value and
never against rendered bytes, because a renderer stamps creation and
modification timestamps, an `/ID` trailer pair, and a producer string into the
file it writes, and a routine dependency bump would then read as corpus drift
(AD-002, DM-4).

What is inside the hash (DM-1): the document's identity fields, the ordered
field values of the transmittal, the per-page text, and the per-page **render
directives** — template identifier, degradation profile, and the profile's
parameters. The directives are inside deliberately (DM-2). Degradation is a
render-stage operation that leaves the text layer unchanged, so a generator
that degraded a different set of pages on every run would still reproduce
identical text and would satisfy FR-021 while being nondeterministic in exactly
the way FR-021 exists to detect.

What is outside: anything a renderer stamps, and any wall-clock value. DM-3
makes the generation date a committed constant rather than a clock read; this
module cannot tell a timestamp from any other string, so it guarantees only the
narrower half — nothing it adds is clock-derived.

Stdlib only, following `model/roster/reader.py`. The digest is compared across
machines, processes, and time, so nothing in the serialization path may depend
on `PYTHONHASHSEED` (keys are sorted), on a platform float repr (floats are
rejected outright), or on a default that a library version could change.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

__all__ = [
    "DocumentModel",
    "DocumentModelError",
    "FieldValue",
    "Page",
    "RenderDirective",
    "canonical_bytes",
    "document_model_hash",
    "parse_canonical_bytes",
]

TOP_LEVEL_KEYS = frozenset({"identity", "fields", "pages"})
FIELD_KEYS = frozenset({"label", "value"})
PAGE_KEYS = frozenset({"text", "render"})
RENDER_KEYS = frozenset({"template_id", "degradation_profile", "parameters"})

_NO_PARAMETERS: Mapping[str, str | int] = MappingProxyType({})


class DocumentModelError(ValueError):
    """Raised when a document model is malformed or unparseable.

    One type for every failure, as `RosterError` is: a caller learns the same
    thing from each of them — this model must not be hashed, and nothing may be
    generated from it.
    """


def _nfc(value: str) -> str:
    """DM-5, applied at construction rather than at serialization.

    Normalizing here means no un-normalized model can exist, so two models
    built from equivalent strings in different normal forms are `==` as well as
    equal-hashing. Normalizing only inside the serializer would leave the
    weaker property — equal digests over unequal objects — which is harder to
    reason about at the call site.
    """
    return unicodedata.normalize("NFC", value)


def _text(value: object, what: str, *, allow_blank: bool) -> str:
    if not isinstance(value, str):
        raise DocumentModelError(f"{what} must be a string, found {type(value).__name__}")
    if not allow_blank and not value.strip():
        raise DocumentModelError(f"{what} must not be empty or whitespace-only")
    return _nfc(value)


def _scalar(value: object, what: str) -> str | int:
    """Strings and integers only — no float, boolean, null, or container.

    Floats are rejected because their shortest-repr serialization is a property
    of the platform rather than of the value; booleans and nulls because JSON
    admits them while a render parameter has no use for them, and admitting a
    type nobody writes is admitting a type nobody checks (DM-3, E001 CS-5).
    """
    if isinstance(value, str):
        return _nfc(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise DocumentModelError(f"{what} must be a string or an integer, found {type(value).__name__}")


def _mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DocumentModelError(f"{what} must be a mapping, found {type(value).__name__}")
    return value


def _sequence(value: object, what: str) -> Sequence[object]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise DocumentModelError(f"{what} must be a sequence, found {type(value).__name__}")
    return value


def _object_with_keys(value: object, keys: frozenset[str], what: str) -> Mapping[str, object]:
    payload = _mapping(value, what)
    if set(payload) != set(keys):
        unexpected = sorted(set(payload) - keys)
        missing = sorted(keys - set(payload))
        raise DocumentModelError(f"{what} keys wrong; unexpected={unexpected} missing={missing}")
    return payload


@dataclass(frozen=True)
class RenderDirective:
    """How one page is rendered: layout template, degradation profile, and the
    profile's parameters. Hashed, per DM-2."""

    template_id: str
    degradation_profile: str
    parameters: Mapping[str, str | int] = field(default=_NO_PARAMETERS)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "template_id", _text(self.template_id, "template_id", allow_blank=False)
        )
        object.__setattr__(
            self,
            "degradation_profile",
            _text(self.degradation_profile, "degradation_profile", allow_blank=False),
        )
        supplied = _mapping(self.parameters, "render parameters")
        normalized = {
            _text(name, "a render parameter name", allow_blank=False): _scalar(
                value, f"render parameter {name!r}"
            )
            for name, value in supplied.items()
        }
        object.__setattr__(self, "parameters", MappingProxyType(normalized))


@dataclass(frozen=True)
class Page:
    """One page's text layer and its render directive."""

    text: str
    directive: RenderDirective

    def __post_init__(self) -> None:
        # Blank is admissible: a page carrying only a raster body still has a
        # (possibly empty) text layer, and an empty page text is a named
        # boundary case rather than a defect.
        object.__setattr__(self, "text", _text(self.text, "page text", allow_blank=True))
        if not isinstance(self.directive, RenderDirective):
            raise DocumentModelError(
                f"page directive must be a RenderDirective, found {type(self.directive).__name__}"
            )


@dataclass(frozen=True)
class FieldValue:
    """One transmittal field. Order is carried by the containing tuple, so this
    is a pair rather than a mapping entry: two documents differing only in the
    order their fields were laid out are two different documents."""

    label: str
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _text(self.label, "field label", allow_blank=False))
        # The value may be blank. `MISSING_OR_BLANK_FIELD` is one of the five
        # irregularity classes the synthetic layer is required to carry, so a
        # model that could not hold a blank value could not express it.
        object.__setattr__(self, "value", _text(self.value, "field value", allow_blank=True))


@dataclass(frozen=True)
class DocumentModel:
    """The pre-render model of one synthetic document (DM-1).

    Frozen, and normalized at construction: the identity mapping and every
    parameter mapping are wrapped read-only, so a model cannot be mutated
    between the moment it is hashed and the moment it is rendered.
    """

    identity: Mapping[str, str]
    fields: tuple[FieldValue, ...]
    pages: tuple[Page, ...]

    def __post_init__(self) -> None:
        supplied = _mapping(self.identity, "identity")
        if not supplied:
            raise DocumentModelError("identity must carry at least one field")
        identity = {
            _text(name, "an identity field name", allow_blank=False): _text(
                value, f"identity field {name!r}", allow_blank=False
            )
            for name, value in supplied.items()
        }
        object.__setattr__(self, "identity", MappingProxyType(identity))

        fields = tuple(_sequence(self.fields, "fields"))
        for index, entry in enumerate(fields):
            if not isinstance(entry, FieldValue):
                raise DocumentModelError(
                    f"fields[{index}] must be a FieldValue, found {type(entry).__name__}"
                )
        object.__setattr__(self, "fields", fields)

        pages = tuple(_sequence(self.pages, "pages"))
        if not pages:
            raise DocumentModelError("a document model must carry at least one page")
        for index, page in enumerate(pages):
            if not isinstance(page, Page):
                raise DocumentModelError(
                    f"pages[{index}] must be a Page, found {type(page).__name__}"
                )
        object.__setattr__(self, "pages", pages)


def _payload(model: DocumentModel) -> dict:
    """The JSON object DM-1 describes.

    Ordered things are arrays and unordered things are objects, deliberately:
    `sort_keys` below reorders object keys, so anything whose order is part of
    the document — the transmittal's fields, the pages — has to be an array or
    the ordering would be silently discarded.
    """
    return {
        "identity": dict(model.identity),
        "fields": [{"label": f.label, "value": f.value} for f in model.fields],
        "pages": [
            {
                "text": page.text,
                "render": {
                    "template_id": page.directive.template_id,
                    "degradation_profile": page.directive.degradation_profile,
                    "parameters": dict(page.directive.parameters),
                },
            }
            for page in model.pages
        ],
    }


def canonical_bytes(model: DocumentModel) -> bytes:
    """Serialize a document model for hashing (DM-5).

    Keys sorted, so insertion order and `PYTHONHASHSEED` cannot move the
    digest; separators compact, so insignificant whitespace cannot; non-ASCII
    preserved rather than escaped; UTF-8 encoded; no indentation and no
    trailing newline. Every one of those is pinned rather than left to a
    default because the digest is compared across machines and across time.
    """
    return json.dumps(
        _payload(model), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def document_model_hash(model: DocumentModel) -> str:
    """Return ``sha256:`` followed by 64 lowercase hexadecimal characters (DM-6).

    The same surface form the roster reader emits, so a downstream comparison
    can treat the two as the same kind of string even though they are digests
    over different things.
    """
    return "sha256:" + hashlib.sha256(canonical_bytes(model)).hexdigest()


def parse_canonical_bytes(raw: bytes) -> DocumentModel:
    """Rebuild a model from its canonical serialization.

    The inverse of `canonical_bytes` for every model that can be constructed,
    which is what makes the serialization checkable as a round trip rather than
    only as a digest: a serializer that dropped a component would still emit a
    stable hash, and only the round trip notices the loss.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DocumentModelError(
            f"canonical document model is not valid UTF-8 JSON: {exc}"
        ) from exc

    top = _object_with_keys(payload, TOP_LEVEL_KEYS, "document model")

    fields = []
    for index, entry in enumerate(_sequence(top["fields"], "fields")):
        parsed = _object_with_keys(entry, FIELD_KEYS, f"fields[{index}]")
        fields.append(FieldValue(label=parsed["label"], value=parsed["value"]))

    pages = []
    for index, entry in enumerate(_sequence(top["pages"], "pages")):
        parsed = _object_with_keys(entry, PAGE_KEYS, f"pages[{index}]")
        render = _object_with_keys(parsed["render"], RENDER_KEYS, f"pages[{index}].render")
        pages.append(
            Page(
                text=parsed["text"],
                directive=RenderDirective(
                    template_id=render["template_id"],
                    degradation_profile=render["degradation_profile"],
                    parameters=_mapping(render["parameters"], f"pages[{index}].render.parameters"),
                ),
            )
        )

    return DocumentModel(
        identity=_mapping(top["identity"], "identity"),
        fields=tuple(fields),
        pages=tuple(pages),
    )
