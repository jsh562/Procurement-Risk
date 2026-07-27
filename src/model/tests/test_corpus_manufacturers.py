"""Every way the manufacturer catalogue can be wrong, and what each one costs.

`load_catalog` is a wall of refusals (FR-037, FR-037b) and the aggregate
coverage gate cannot see past it: the module sat at 72% while its package
averaged 92%, which is the case `verify.yml`'s per-package gate exists to
surface and the case it still missed, because neither gate carries a per-file
floor. Every `_fail(...)` branch below is therefore exercised by name.

**The refusals are the interface.** The catalogue is read at import, so a
malformed one is an import-time failure of every module that would name a
manufacturer; a loader that returned a partial catalogue instead would emit a
corpus whose manufacturers the validator could not resolve, discoverable only by
regenerating. Each case here asserts the *message*, not merely the exception
type, because one exception type is shared by twenty-odd distinct defects and a
test that accepted any of them would pass while the loader diagnosed the wrong
one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from model.corpus.manufacturers import (
    MANUFACTURERS,
    PART_NUMBER_PATTERN,
    Manufacturer,
    ManufacturerCatalogError,
    canonical_key_for_printed_name,
    format_part_number,
    load_catalog,
    manufacturers_for_category,
    printed_names,
    uncovered_categories,
)

# --------------------------------------------------------------------------
# A well-formed catalogue, and the one-field mutations of it
# --------------------------------------------------------------------------


def _entry(**overrides: object) -> dict[str, object]:
    """One valid entry. Overridden a field at a time, so each case names one defect."""
    record: dict[str, object] = {
        "canonical_name": "Verrikon Electric",
        "aliases": ["VERRIKON ELECTRIC", "Verrikon Elec."],
        "part_number_prefix": "VRK",
        "categories": ["SWITCHGEAR"],
    }
    record.update(overrides)
    return record


def _catalog(manufacturers: Mapping[str, object] | None = None, **top: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "manufacturers": {"VRK": _entry()} if manufacturers is None else manufacturers
    }
    payload.update(top)
    return payload


def _write(tmp_path: Path, payload: object) -> Path:
    target = tmp_path / "manufacturer-catalog.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _refuses(tmp_path: Path, payload: object, fragment: str) -> str:
    with pytest.raises(ManufacturerCatalogError) as raised:
        load_catalog(_write(tmp_path, payload))
    message = str(raised.value)
    assert fragment in message, message
    return message


def test_a_well_formed_catalogue_loads(tmp_path: Path) -> None:
    """The control. Without it every refusal below could be refusing for one reason."""
    loaded = load_catalog(_write(tmp_path, _catalog()))
    assert set(loaded) == {"VRK"}
    assert loaded["VRK"].canonical_name == "Verrikon Electric"
    assert loaded["VRK"].printed_names == (
        "Verrikon Electric",
        "VERRIKON ELECTRIC",
        "Verrikon Elec.",
    )
    # Categories are sorted on the way in, so two catalogues differing only in
    # the order they list a maker's categories load to the same record.
    assert loaded["VRK"].categories == ("SWITCHGEAR",)


def test_categories_are_ordered_on_load_rather_than_as_written(tmp_path: Path) -> None:
    payload = _catalog({"VRK": _entry(categories=["SWITCHGEAR", "HYDRONIC_PUMP"])})
    loaded = load_catalog(_write(tmp_path, payload))
    assert loaded["VRK"].categories == ("HYDRONIC_PUMP", "SWITCHGEAR")


# --------------------------------------------------------------------------
# Reaching the file at all
# --------------------------------------------------------------------------


def test_an_absent_catalogue_is_reported_as_unreadable(tmp_path: Path) -> None:
    with pytest.raises(ManufacturerCatalogError, match="cannot read the manufacturer catalogue"):
        load_catalog(tmp_path / "does-not-exist.json")


def test_a_directory_where_the_catalogue_should_be_is_reported_as_unreadable(
    tmp_path: Path,
) -> None:
    """`read_bytes` on a directory raises `OSError`, not `FileNotFoundError`."""
    (tmp_path / "manufacturer-catalog.json").mkdir()
    with pytest.raises(ManufacturerCatalogError, match="cannot read the manufacturer catalogue"):
        load_catalog(tmp_path / "manufacturer-catalog.json")


def test_a_corpus_root_that_does_not_exist_is_reported_as_unresolvable(tmp_path: Path) -> None:
    """The default-path branch: no explicit `path`, and the root cannot be resolved."""
    with pytest.raises(ManufacturerCatalogError, match="cannot resolve the manufacturer catalogue"):
        load_catalog(root=tmp_path / "no-such-corpus-root")


def test_the_catalogue_resolves_under_an_explicit_corpus_root(tmp_path: Path) -> None:
    """The other side of that branch, so the resolution is evidenced and not only its failure."""
    synthetic = tmp_path / "synthetic"
    synthetic.mkdir()
    (synthetic / "manufacturer-catalog.json").write_text(json.dumps(_catalog()), encoding="utf-8")
    assert set(load_catalog(root=tmp_path)) == {"VRK"}


def test_bytes_that_are_not_utf8_are_refused(tmp_path: Path) -> None:
    target = tmp_path / "manufacturer-catalog.json"
    target.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(ManufacturerCatalogError, match="is not valid UTF-8 JSON"):
        load_catalog(target)


def test_text_that_is_not_json_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "manufacturer-catalog.json"
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManufacturerCatalogError, match="is not valid UTF-8 JSON"):
        load_catalog(target)


# --------------------------------------------------------------------------
# The document's shape
# --------------------------------------------------------------------------


def test_a_payload_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    _refuses(tmp_path, ["VRK"], "must be an object, found list")


def test_an_unexpected_top_level_key_is_refused(tmp_path: Path) -> None:
    """Closed rather than tolerant: a key nothing reads is a claim nothing enforces."""
    _refuses(tmp_path, _catalog(version=2), "unexpected top-level keys ['version']")


def test_manufacturers_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    _refuses(tmp_path, {"manufacturers": ["VRK"]}, "manufacturers must be an object, found list")


def test_an_empty_manufacturers_object_is_refused(tmp_path: Path) -> None:
    """Empty is not a degenerate pass: every category would be uncovered instead."""
    _refuses(tmp_path, {"manufacturers": {}}, "manufacturers must not be empty")


# --------------------------------------------------------------------------
# One entry's shape
# --------------------------------------------------------------------------


def test_a_key_that_is_not_three_upper_case_letters_is_refused(tmp_path: Path) -> None:
    _refuses(tmp_path, _catalog({"vrk": _entry()}), "is not three upper-case letters")


def test_an_entry_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    _refuses(tmp_path, _catalog({"VRK": "Verrikon"}), "must be an object, found str")


def test_an_entry_carrying_an_unexpected_key_is_refused(tmp_path: Path) -> None:
    _refuses(tmp_path, _catalog({"VRK": _entry(founded=1954)}), "unexpected keys ['founded']")


def test_an_entry_missing_a_required_key_is_refused(tmp_path: Path) -> None:
    incomplete = _entry()
    del incomplete["categories"]
    _refuses(tmp_path, _catalog({"VRK": incomplete}), "is missing ['categories']")


def test_an_entry_missing_its_aliases_is_refused(tmp_path: Path) -> None:
    """Absent aliases and empty aliases are different defects with different messages."""
    incomplete = _entry()
    del incomplete["aliases"]
    _refuses(tmp_path, _catalog({"VRK": incomplete}), "is missing ['aliases']")


@pytest.mark.parametrize("value", ["", "   ", 7, None])
def test_an_empty_or_non_string_canonical_name_is_refused(tmp_path: Path, value: object) -> None:
    _refuses(tmp_path, _catalog({"VRK": _entry(canonical_name=value)}), "empty canonical_name")


@pytest.mark.parametrize("value", ["vrk", "VRKX", "VR", "", 3])
def test_a_malformed_part_number_prefix_is_refused(tmp_path: Path, value: object) -> None:
    _refuses(
        tmp_path,
        _catalog({"VRK": _entry(part_number_prefix=value)}),
        "malformed part_number_prefix",
    )


def test_a_prefix_that_is_not_the_key_is_refused(tmp_path: Path) -> None:
    """The key IS the prefix; two names for one value can drift apart."""
    _refuses(
        tmp_path,
        _catalog({"VRK": _entry(part_number_prefix="ZZZ")}),
        "declares part_number_prefix 'ZZZ'",
    )


@pytest.mark.parametrize("value", [[], "VERRIKON ELECTRIC", None, {}])
def test_an_empty_or_non_list_alias_set_is_refused(tmp_path: Path, value: object) -> None:
    """A string is a `Sequence` and must not be mistaken for a one-alias list."""
    _refuses(tmp_path, _catalog({"VRK": _entry(aliases=value)}), "must carry at least one alias")


@pytest.mark.parametrize("value", ["", "  ", 5, None])
def test_an_empty_or_non_string_alias_is_refused(tmp_path: Path, value: object) -> None:
    _refuses(tmp_path, _catalog({"VRK": _entry(aliases=[value])}), "has an empty alias")


@pytest.mark.parametrize("value", [[], "SWITCHGEAR", None, {}])
def test_an_empty_or_non_list_category_set_is_refused(tmp_path: Path, value: object) -> None:
    _refuses(
        tmp_path,
        _catalog({"VRK": _entry(categories=value)}),
        "must name at least one equipment category",
    )


@pytest.mark.parametrize("value", ["", " ", 1, None])
def test_an_empty_or_non_string_category_is_refused(tmp_path: Path, value: object) -> None:
    _refuses(
        tmp_path, _catalog({"VRK": _entry(categories=[value])}), "has an empty equipment category"
    )


def test_a_repeated_category_is_refused(tmp_path: Path) -> None:
    _refuses(
        tmp_path,
        _catalog({"VRK": _entry(categories=["SWITCHGEAR", "SWITCHGEAR"])}),
        "repeats an equipment category",
    )


def test_a_repeated_printed_spelling_within_one_entry_is_refused(tmp_path: Path) -> None:
    """A literal repeat is a data error; a *normalized* one is the point of an alias."""
    _refuses(
        tmp_path,
        _catalog({"VRK": _entry(aliases=["Verrikon Electric"])}),
        "repeats a printed spelling",
    )


def test_an_alias_folding_onto_its_own_canonical_name_is_admitted(tmp_path: Path) -> None:
    """The negative control for the case above: `VERRIKON ELECTRIC` is why aliases exist."""
    loaded = load_catalog(_write(tmp_path, _catalog()))
    assert "VERRIKON ELECTRIC" in loaded["VRK"].printed_names


# --------------------------------------------------------------------------
# The two cross-entry refusals
# --------------------------------------------------------------------------


def test_a_catalogue_name_on_the_rosters_real_firm_list_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E001's exclusion list reaches manufacturers, and is not restated here."""
    from model.corpus import manufacturers as module

    monkeypatch.setattr(module, "load_exclusions", lambda: ["Verrikon Electric"])
    _refuses(tmp_path, _catalog(), "names an excluded real firm")


def test_an_alias_on_the_rosters_real_firm_list_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The list is applied to every printed spelling, not only to canonical names."""
    from model.corpus import manufacturers as module

    monkeypatch.setattr(module, "load_exclusions", lambda: ["Verrikon Elec."])
    _refuses(tmp_path, _catalog(), "names an excluded real firm")


def test_one_spelling_claimed_by_two_manufacturers_is_refused(tmp_path: Path) -> None:
    """Undecidable is worse than absent: a resolver blocks on this field."""
    payload = _catalog(
        {
            "AAA": _entry(canonical_name="Ashvale Industrial", part_number_prefix="AAA"),
            "VRK": _entry(aliases=["Ashvale Industrial"]),
        }
    )
    _refuses(tmp_path, payload, "is claimed by both AAA and VRK")


def test_a_collision_under_normalization_alone_is_still_refused(tmp_path: Path) -> None:
    """Matching is the roster's normalization, so a case-only difference still collides."""
    payload = _catalog(
        {
            "AAA": _entry(canonical_name="Ashvale Industrial", part_number_prefix="AAA"),
            "VRK": _entry(aliases=["ASHVALE INDUSTRIAL"]),
        }
    )
    _refuses(tmp_path, payload, "is claimed by both AAA and VRK")


# --------------------------------------------------------------------------
# The accessors, over the committed catalogue
# --------------------------------------------------------------------------


def test_manufacturers_for_category_returns_keys_in_ascending_order() -> None:
    category = sorted(next(iter(MANUFACTURERS.values())).categories)[0]
    keys = manufacturers_for_category(category)
    assert keys and list(keys) == sorted(keys)


def test_manufacturers_for_a_category_nobody_makes_raises_rather_than_returning_empty() -> None:
    """`()` would let the generator write a blank manufacturer and call it an irregularity."""
    with pytest.raises(ManufacturerCatalogError, match="no manufacturer in the catalogue makes"):
        manufacturers_for_category("NO_SUCH_CATEGORY")


def test_printed_names_of_an_unknown_key_raises() -> None:
    with pytest.raises(ManufacturerCatalogError, match="is not a manufacturer in the catalogue"):
        printed_names("ZZZ")


def test_printed_names_of_a_committed_key_leads_with_the_canonical_name() -> None:
    key = sorted(MANUFACTURERS)[0]
    assert printed_names(key)[0] == MANUFACTURERS[key].canonical_name


def test_format_part_number_of_an_unknown_key_raises() -> None:
    with pytest.raises(ManufacturerCatalogError, match="is not a manufacturer in the catalogue"):
        format_part_number("ZZZ", 1)


def test_a_negative_serial_raises_rather_than_wrapping() -> None:
    """Modulo would silently turn -1 into a plausible number nobody asked for."""
    key = sorted(MANUFACTURERS)[0]
    with pytest.raises(ManufacturerCatalogError, match="must not be negative"):
        format_part_number(key, -1)


def test_a_serial_wider_than_the_field_is_reduced_rather_than_refused() -> None:
    key = sorted(MANUFACTURERS)[0]
    assert format_part_number(key, 1_234_567) == f"{key}-34567"
    assert PART_NUMBER_PATTERN.match(format_part_number(key, 0))


def test_canonical_key_for_printed_name_resolves_every_committed_spelling() -> None:
    for key, entry in MANUFACTURERS.items():
        for printed in entry.printed_names:
            assert canonical_key_for_printed_name(printed) == key


def test_canonical_key_for_a_spelling_nobody_claims_is_none() -> None:
    assert canonical_key_for_printed_name("Zephyrline Absent") is None


def test_uncovered_categories_reports_only_the_unbacked_ones() -> None:
    backed = sorted({category for entry in MANUFACTURERS.values() for category in entry.categories})
    assert uncovered_categories(backed) == ()
    assert uncovered_categories([*backed, "NO_SUCH_CATEGORY"]) == ("NO_SUCH_CATEGORY",)


def test_printed_names_orders_canonical_before_aliases() -> None:
    record = Manufacturer(
        key="AAA",
        canonical_name="Ashvale Industrial",
        aliases=("Ashvale", "ASHVALE INDUSTRIAL"),
        part_number_prefix="AAA",
        categories=("SWITCHGEAR",),
    )
    assert record.printed_names == ("Ashvale Industrial", "Ashvale", "ASHVALE INDUSTRIAL")
