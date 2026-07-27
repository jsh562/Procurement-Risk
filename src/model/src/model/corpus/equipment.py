"""Equipment category to MasterFormat section, from the committed map.

FR-026 / VR-048. A synthetic submittal that named an equipment category with no
specification section behind it would look complete and link to nothing: the
specification-to-submittal join every downstream epic needs would have no
right-hand side. This module owns the one mapping that makes that join
constructible.

**Both halves of VR-048 matter, and they fail differently.** That every item's
category is a key in this map is enforced here, at generation time, by
`section_for_category` raising on anything outside the closed set — a document
naming an unmapped category is never written. That every map *value* is a
`masterformat_section` the real layer actually holds is enforced against the
real manifest by `unbacked_sections`, because a map pointing at a section the
corpus does not carry would satisfy the first half completely while leaving the
join with a dangling reference.

The map is read at import, as `sources.py` reads its policy and `codes.py` its
vocabulary: a missing or malformed map is an import-time failure of every
module that would name an equipment category. The real manifest is **not** read
at import — it is a corpus artifact rather than a generation input, and reading
it eagerly would make importing this module depend on a complete corpus.

Stdlib only, following `model/roster/reader.py`: one error type, frozen
mapping, results ordered deterministically.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from model.corpus.manifest import GENERATION_INPUT_PATHS, MASTERFORMAT_PATTERN
from model.corpus.paths import (
    MANIFEST_FILENAME,
    CorpusPathError,
    corpus_root,
    repository_relative_path,
    resolve_within,
)

__all__ = [
    "CATEGORIES",
    "CATEGORY_SECTIONS",
    "EQUIPMENT_MAP_INPUT_PATH",
    "REAL_MANIFEST_RELATIVE_PATH",
    "EquipmentMapError",
    "load_category_map",
    "real_layer_sections",
    "section_for_category",
    "unbacked_sections",
]

# Taken from the closed tuple `manifest.py` holds rather than written out
# again, so the path named here and the key recorded in every SYNTHETIC entry
# are the same string.
EQUIPMENT_MAP_INPUT_PATH = next(
    path for path in GENERATION_INPUT_PATHS if path.endswith("equipment-category-map.json")
)

# The real layer's one location, corpus-relative. A validator-owned literal,
# resolved through `resolve_within` like every other corpus path (VR-009).
REAL_MANIFEST_RELATIVE_PATH = f"real/ufgs/{MANIFEST_FILENAME}"


class EquipmentMapError(ValueError):
    """Raised when the category map is missing, malformed, or asked for an unknown key.

    One type for every failure, as `RosterError` and `ManifestError` are: a
    caller learns the same thing from each of them — no material item may be
    written from this map.
    """


def load_category_map(path: Path | None = None, *, root: Path | None = None) -> Mapping[str, str]:
    """Read and validate `equipment-category-map.json`.

    Section values are checked against `MASTERFORMAT_PATTERN` — the same
    pattern the manifest holds a REAL entry's `masterformat_section` to — so a
    value that could not possibly appear in the real manifest fails here rather
    than surviving until the cross-check has a corpus to compare against.
    """
    if path is not None:
        target = Path(path)
    else:
        try:
            target = repository_relative_path(EQUIPMENT_MAP_INPUT_PATH, root)
        except CorpusPathError as exc:
            raise EquipmentMapError(f"cannot resolve the equipment-category map: {exc}") from exc

    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise EquipmentMapError(f"cannot read the equipment-category map {target}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EquipmentMapError(f"{target} is not valid UTF-8 JSON: {exc}") from exc

    if not isinstance(payload, Mapping):
        raise EquipmentMapError(
            f"the equipment-category map must be an object, found {type(payload).__name__}"
        )
    unexpected = sorted(set(payload) - {"categories"})
    if unexpected:
        raise EquipmentMapError(f"{target} carries unexpected top-level keys {unexpected}")

    categories = payload.get("categories")
    if not isinstance(categories, Mapping):
        raise EquipmentMapError(f"categories must be an object, found {type(categories).__name__}")
    if not categories:
        raise EquipmentMapError("categories must not be empty")

    resolved: dict[str, str] = {}
    for token in sorted(categories):
        if not isinstance(token, str) or not token.strip():
            raise EquipmentMapError(f"an equipment-category token is empty: {token!r}")
        section = categories[token]
        if not isinstance(section, str):
            raise EquipmentMapError(
                f"categories[{token!r}] must be a string, found {type(section).__name__}"
            )
        if not MASTERFORMAT_PATTERN.fullmatch(section):
            raise EquipmentMapError(
                f"categories[{token!r}] must match {MASTERFORMAT_PATTERN.pattern}, "
                f"found {section!r}"
            )
        resolved[token] = section
    return MappingProxyType(resolved)


CATEGORY_SECTIONS: Mapping[str, str] = load_category_map()

# Ascending, so a caller iterating the categories cannot inherit a dict order
# that a re-authored file would silently change.
CATEGORIES: tuple[str, ...] = tuple(sorted(CATEGORY_SECTIONS))


def section_for_category(category: str) -> str:
    """The MasterFormat section a material item's equipment category answers.

    Raises rather than returning a default or `None`: a material item whose
    category is not in the closed set has no section, and a generator that
    substituted a placeholder would emit a document satisfying FR-023 while
    breaking FR-026 invisibly.
    """
    try:
        return CATEGORY_SECTIONS[category]
    except (KeyError, TypeError):
        raise EquipmentMapError(
            f"equipment category {category!r} is not in the committed map; "
            f"known categories: {list(CATEGORIES)}"
        ) from None


def real_layer_sections(root: Path | None = None) -> frozenset[str]:
    """Every `masterformat_section` the vendored real layer records.

    Read from the real manifest rather than from the retrieval policy: the
    policy states what was *targeted*, and FR-026 is about what the corpus
    actually holds. A section that was targeted and then excluded is in one and
    not the other, which is the case this distinction exists for.
    """
    base = corpus_root(root)
    try:
        target = resolve_within(base, REAL_MANIFEST_RELATIVE_PATH)
    except CorpusPathError as exc:
        raise EquipmentMapError(f"cannot resolve the real manifest: {exc}") from exc
    try:
        payload = json.loads(target.read_bytes().decode("utf-8"))
    except OSError as exc:
        raise EquipmentMapError(f"cannot read the real manifest {target}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EquipmentMapError(f"{target} is not valid UTF-8 JSON: {exc}") from exc

    entries = payload.get("entries") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list) or not entries:
        raise EquipmentMapError(f"{target} carries no entries to read sections from")
    sections = {
        entry.get("masterformat_section")
        for entry in entries
        if isinstance(entry, Mapping) and isinstance(entry.get("masterformat_section"), str)
    }
    if not sections:
        raise EquipmentMapError(f"{target} records no masterformat_section values")
    return frozenset(sections)


def unbacked_sections(root: Path | None = None) -> tuple[tuple[str, str], ...]:
    """VR-048's second half: mapped sections the real layer does not hold.

    Returns `(category, section)` pairs, ascending, rather than raising, so a
    caller can report every offending category at once (VR-056). An empty tuple
    is the passing outcome.
    """
    present = real_layer_sections(root)
    return tuple(
        (category, CATEGORY_SECTIONS[category])
        for category in CATEGORIES
        if CATEGORY_SECTIONS[category] not in present
    )
