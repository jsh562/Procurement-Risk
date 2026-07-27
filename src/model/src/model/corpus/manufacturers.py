"""Manufacturer identity and part numbers, from the committed catalogue.

FR-037 / VR-069…VR-073. A submittal that names a material item without saying
who makes it and what it is called in that maker's catalogue is complete as a
routing form and useless as one side of an identity join: E009 blocks candidate
pairs on manufacturer and part-number prefix, and E003 already carries both as
`NOT NULL` columns on a purchase-order line. Printing them on the document is
what gives that join a left-hand side.

**Aliases are ordinary naming, not an irregularity.** A real submittal package
spells one manufacturer three ways across three pages, and an identity resolver
exists precisely to reconcile that. So the catalogue carries alias spellings and
the generator prints them, but nothing here is recorded as an irregularity
class — FR-030's set of five is closed, and widening it to describe normal
variation would make "irregularity" mean two different things.

**Part numbers are prefix-blockable by construction.** Every number a
manufacturer issues starts with that manufacturer's prefix, so blocking on the
prefix recovers the manufacturer even when the printed name is an alias the
resolver has not yet normalized. That is the property E009's blocking key
depends on, and it is a property of the catalogue rather than of any one
document.

**Synthetic names are enforced, not asserted.** Every canonical name and every
alias is checked against the roster's real-firm exclusion list after the
roster's own normalization — the same backstop E001 applies to vendor names,
reused rather than restated, so the two cannot drift into disagreeing about
what counts as a real firm.

The catalogue is read at import, as `equipment.py` reads its map and
`sources.py` its policy: a missing or malformed catalogue is an import-time
failure of every module that would name a manufacturer. Stdlib only, one error
type, results ordered deterministically.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from model.corpus.manifest import GENERATION_INPUT_PATHS
from model.corpus.paths import (
    CorpusPathError,
    repository_relative_path,
)
from model.roster.naming import load_exclusions, normalize

__all__ = [
    "MANUFACTURER_CATALOG_INPUT_PATH",
    "MANUFACTURERS",
    "PART_NUMBER_PATTERN",
    "PREFIX_PATTERN",
    "Manufacturer",
    "ManufacturerCatalogError",
    "canonical_key_for_printed_name",
    "format_part_number",
    "load_catalog",
    "manufacturers_for_category",
    "printed_names",
    "uncovered_categories",
]

# Taken from the closed tuple `manifest.py` holds rather than written out again,
# so the path named here and the key recorded in every SYNTHETIC entry's
# `generation_inputs` are the same string.
MANUFACTURER_CATALOG_INPUT_PATH = next(
    path for path in GENERATION_INPUT_PATHS if path.endswith("manufacturer-catalog.json")
)

# Three upper-case letters. Stated as a pattern rather than left to the data so
# a catalogue edit that would make part numbers unblockable fails at load.
PREFIX_PATTERN = re.compile(r"^[A-Z]{3}$")

# `<prefix>-<five digits>`. The prefix half is what E009 blocks on; the digit
# half only has to be stable and distinguishable.
PART_NUMBER_PATTERN = re.compile(r"^[A-Z]{3}-[0-9]{5}$")

_PART_NUMBER_DIGITS = 5


class ManufacturerCatalogError(ValueError):
    """Raised when the catalogue is missing, malformed, or asked for an unknown key.

    One type for every failure, as `EquipmentMapError` and `RosterError` are: a
    caller learns the same thing from each of them — no material item may be
    written from this catalogue.
    """


@dataclass(frozen=True)
class Manufacturer:
    """One catalogue entry: who they are, what they are also called, what they make."""

    key: str
    canonical_name: str
    aliases: tuple[str, ...]
    part_number_prefix: str
    categories: tuple[str, ...]

    @property
    def printed_names(self) -> tuple[str, ...]:
        """Every spelling that may appear on a document, canonical first.

        Ordered rather than a set: the generator indexes into this with a
        seeded draw, so the order is part of what makes a run reproducible.
        """
        return (self.canonical_name, *self.aliases)


def _fail(message: str) -> ManufacturerCatalogError:
    return ManufacturerCatalogError(message)


def load_catalog(
    path: Path | None = None, *, root: Path | None = None
) -> Mapping[str, Manufacturer]:
    """Read and validate `manufacturer-catalog.json`.

    Every check here fails the load rather than returning a partial catalogue.
    A generator that ran against half a catalogue would emit documents naming
    manufacturers that the validator could not resolve, and the corpus would
    have to be regenerated to find out.
    """
    if path is not None:
        target = Path(path)
    else:
        try:
            target = repository_relative_path(MANUFACTURER_CATALOG_INPUT_PATH, root)
        except CorpusPathError as exc:
            raise _fail(f"cannot resolve the manufacturer catalogue: {exc}") from exc

    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise _fail(f"cannot read the manufacturer catalogue {target}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(f"{target} is not valid UTF-8 JSON: {exc}") from exc

    if not isinstance(payload, Mapping):
        raise _fail(f"the manufacturer catalogue must be an object, found {type(payload).__name__}")
    unexpected = sorted(set(payload) - {"manufacturers"})
    if unexpected:
        raise _fail(f"{target} carries unexpected top-level keys {unexpected}")

    entries = payload.get("manufacturers")
    if not isinstance(entries, Mapping):
        raise _fail(f"manufacturers must be an object, found {type(entries).__name__}")
    if not entries:
        raise _fail("manufacturers must not be empty")

    excluded = {normalize(name) for name in load_exclusions()}

    resolved: dict[str, Manufacturer] = {}
    seen_printed: dict[str, str] = {}
    for key in sorted(entries):
        entry = entries[key]
        if not PREFIX_PATTERN.match(key):
            raise _fail(f"manufacturer key {key!r} is not three upper-case letters")
        if not isinstance(entry, Mapping):
            raise _fail(f"manufacturer {key} must be an object, found {type(entry).__name__}")
        allowed = {"aliases", "canonical_name", "categories", "part_number_prefix"}
        extra = sorted(set(entry) - allowed)
        if extra:
            raise _fail(f"manufacturer {key} carries unexpected keys {extra}")
        missing = sorted(allowed - set(entry))
        if missing:
            raise _fail(f"manufacturer {key} is missing {missing}")

        canonical = entry["canonical_name"]
        if not isinstance(canonical, str) or not canonical.strip():
            raise _fail(f"manufacturer {key} has an empty canonical_name")

        prefix = entry["part_number_prefix"]
        if not isinstance(prefix, str) or not PREFIX_PATTERN.match(prefix):
            raise _fail(f"manufacturer {key} has a malformed part_number_prefix {prefix!r}")
        # The key IS the prefix. Two names for one value would let a catalogue
        # edit change one and not the other, and a part number would then block
        # to a manufacturer the entry does not describe.
        if prefix != key:
            raise _fail(f"manufacturer {key} declares part_number_prefix {prefix!r}")

        aliases = entry["aliases"]
        if not isinstance(aliases, Sequence) or isinstance(aliases, str) or not aliases:
            raise _fail(f"manufacturer {key} must carry at least one alias")
        for alias in aliases:
            if not isinstance(alias, str) or not alias.strip():
                raise _fail(f"manufacturer {key} has an empty alias")

        categories = entry["categories"]
        if not isinstance(categories, Sequence) or isinstance(categories, str) or not categories:
            raise _fail(f"manufacturer {key} must name at least one equipment category")
        for category in categories:
            if not isinstance(category, str) or not category.strip():
                raise _fail(f"manufacturer {key} has an empty equipment category")
        if len(set(categories)) != len(categories):
            raise _fail(f"manufacturer {key} repeats an equipment category")

        record = Manufacturer(
            key=key,
            canonical_name=canonical,
            aliases=tuple(aliases),
            part_number_prefix=prefix,
            categories=tuple(sorted(categories)),
        )

        # A literal repeat inside one entry is a data error; a *normalized*
        # repeat is not. "VERRIKON ELECTRIC" folds onto "Verrikon Electric" by
        # construction, and that is the whole point of carrying it — the alias
        # exists to give a resolver something to normalize. Only collisions
        # across two manufacturers are defects, because those make the
        # resolver's job undecidable on the field it blocks on.
        if len(set(record.printed_names)) != len(record.printed_names):
            raise _fail(f"manufacturer {key} repeats a printed spelling")

        for printed in record.printed_names:
            folded = normalize(printed)
            # The roster's backstop, reused rather than restated (VR-073).
            if folded in excluded:
                raise _fail(f"manufacturer {key} names an excluded real firm: {printed!r}")
            owner = seen_printed.get(folded)
            if owner is not None and owner != key:
                raise _fail(f"{printed!r} is claimed by both {owner} and {key}")
            seen_printed[folded] = key

        resolved[key] = record

    return MappingProxyType(resolved)


MANUFACTURERS: Mapping[str, Manufacturer] = load_catalog()


def manufacturers_for_category(category: str) -> tuple[str, ...]:
    """Catalogue keys that make `category`, in ascending key order.

    Raises rather than returning empty: a category with no manufacturer behind
    it is a catalogue defect, and returning `()` would let the generator write
    an item with a blank manufacturer and call it an irregularity.
    """
    keys = tuple(key for key, entry in MANUFACTURERS.items() if category in entry.categories)
    if not keys:
        raise _fail(f"no manufacturer in the catalogue makes {category!r}")
    return keys


def printed_names(key: str) -> tuple[str, ...]:
    """Every spelling `key` may appear under, canonical first."""
    try:
        return MANUFACTURERS[key].printed_names
    except KeyError as exc:
        raise _fail(f"{key!r} is not a manufacturer in the catalogue") from exc


def format_part_number(key: str, serial: int) -> str:
    """`<prefix>-<five digits>` for `key`.

    The serial is reduced modulo the digit width rather than rejected: the
    caller draws it from a seeded generator whose range is its own business,
    and a generator that had to know this width would be coupled to a format
    that is this module's to define.
    """
    if key not in MANUFACTURERS:
        raise _fail(f"{key!r} is not a manufacturer in the catalogue")
    if serial < 0:
        raise _fail(f"part-number serial must not be negative: {serial}")
    return f"{key}-{serial % (10**_PART_NUMBER_DIGITS):0{_PART_NUMBER_DIGITS}d}"


def canonical_key_for_printed_name(name: str) -> str | None:
    """Resolve a printed spelling back to its catalogue key, or `None`.

    Normalization is the roster's, so "VERRIKON ELECTRIC", "Verrikon Elec." and
    "Verrikon Electric" all resolve to the same entry. This is the validator's
    side of FR-037a: it re-derives the manufacturer from what was printed
    instead of trusting what the generator recorded.
    """
    folded = normalize(name)
    for key, entry in MANUFACTURERS.items():
        if any(normalize(printed) == folded for printed in entry.printed_names):
            return key
    return None


def uncovered_categories(categories: Sequence[str]) -> tuple[str, ...]:
    """Categories with no manufacturer behind them, ascending.

    The catalogue's half of VR-072. `equipment.py` checks that every category
    maps to a section the real layer holds; this checks that every category
    also has someone who makes it, so a synthetic item can always be written
    with both a section and a manufacturer.
    """
    backed = {category for entry in MANUFACTURERS.values() for category in entry.categories}
    return tuple(sorted(set(categories) - backed))
