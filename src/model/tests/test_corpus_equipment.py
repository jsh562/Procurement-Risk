"""FR-026 / VR-048: the equipment-category map and the sections behind it.

**The two halves of VR-048 fail differently, and both need cases.** That every
item's category is a key in the committed map is enforced at generation time by
`section_for_category`, which raises rather than substituting a placeholder.
That every map *value* is a `masterformat_section` the real layer actually holds
is enforced against the real manifest by `unbacked_sections` — the first without
the second would let items point at specifications the corpus does not carry,
leaving the specification-to-submittal join with a dangling right-hand side.

The loader's refusals are exercised here rather than described. A map that is
missing, unreadable, malformed, or names a section of the wrong shape is an
import-time failure of every module that would name an equipment category, and
a loader whose refusals were never observed would be a loader nobody could rely
on to refuse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from model.corpus.equipment import (
    CATEGORIES,
    CATEGORY_SECTIONS,
    EquipmentMapError,
    load_category_map,
    real_layer_sections,
    section_for_category,
    unbacked_sections,
)
from model.corpus.manifest import MANIFEST_FILENAME
from model.corpus.paths import CorpusPathError

VALID = {"categories": {"WATER_CHILLER": "23 64 10", "SWITCHBOARD": "26 24 13"}}


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- the committed map -------------------------------------------------------


def test_the_committed_map_is_non_empty_and_ordered() -> None:
    assert tuple(sorted(CATEGORY_SECTIONS)) == CATEGORIES
    assert CATEGORIES, "the committed map holds no category"


def test_section_for_category_resolves_a_known_category() -> None:
    assert section_for_category(CATEGORIES[0]) == CATEGORY_SECTIONS[CATEGORIES[0]]


@pytest.mark.parametrize("category", ["", "NOT_A_CATEGORY", None])
def test_section_for_category_refuses_anything_outside_the_closed_set(category: object) -> None:
    """No default and no placeholder: a substituted section would emit a document
    satisfying FR-023 while breaking FR-026 invisibly."""
    with pytest.raises(EquipmentMapError):
        section_for_category(category)  # type: ignore[arg-type]


# --- the loader's refusals ---------------------------------------------------


def test_load_category_map_reads_an_explicit_path(tmp_path: Path) -> None:
    loaded = load_category_map(_write(tmp_path / "map.json", VALID))
    assert dict(loaded) == VALID["categories"]


def test_load_category_map_refuses_an_unresolvable_root(tmp_path: Path) -> None:
    with pytest.raises(EquipmentMapError):
        load_category_map(root=tmp_path / "no-such-corpus-root")


def test_load_category_map_refuses_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(EquipmentMapError):
        load_category_map(tmp_path / "absent.json")


def test_load_category_map_refuses_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    path.write_bytes(b"{ not json\n")
    with pytest.raises(EquipmentMapError):
        load_category_map(path)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([], id="not-an-object"),
        pytest.param({"categories": {}, "extra": 1}, id="unexpected-top-level-key"),
        pytest.param({"categories": []}, id="categories-not-an-object"),
        pytest.param({"categories": {}}, id="categories-empty"),
        pytest.param({"categories": {"  ": "23 64 10"}}, id="blank-token"),
        pytest.param({"categories": {"WATER_CHILLER": 236410}}, id="section-not-a-string"),
        pytest.param({"categories": {"WATER_CHILLER": "236410"}}, id="section-wrong-shape"),
    ],
)
def test_load_category_map_refuses_a_malformed_document(tmp_path: Path, payload: object) -> None:
    with pytest.raises(EquipmentMapError):
        load_category_map(_write(tmp_path / "map.json", payload))


# --- VR-048's second half: the sections the real layer actually holds --------


def _real_manifest(root: Path, entries: list[dict]) -> Path:
    location = root / "real" / "ufgs"
    location.mkdir(parents=True)
    path = location / MANIFEST_FILENAME
    path.write_text(
        json.dumps({"location_id": "real/ufgs", "layer": "REAL", "entries": entries}),
        encoding="utf-8",
    )
    return path


def test_real_layer_sections_reads_the_committed_manifest() -> None:
    """Read from the manifest rather than from the retrieval policy: the policy
    states what was targeted, and FR-026 is about what the corpus holds."""
    sections = real_layer_sections()
    assert sections and all(len(section) == 8 for section in sections)


def test_the_committed_map_points_only_at_sections_the_real_layer_holds() -> None:
    assert unbacked_sections() == ()


def test_unbacked_sections_names_every_category_the_real_layer_cannot_back(
    tmp_path: Path,
) -> None:
    """The failing direction, over a real manifest holding one section."""
    root = tmp_path / "corpus"
    _real_manifest(root, [{"masterformat_section": "01 33 00"}])
    unbacked = unbacked_sections(root)
    assert len(unbacked) == len(CATEGORY_SECTIONS)
    assert all(section != "01 33 00" for _, section in unbacked)


def test_real_layer_sections_refuses_a_root_that_is_not_a_corpus(tmp_path: Path) -> None:
    """The root is checked before this module resolves anything under it.

    The refusal therefore carries `CorpusPathError` — the path module's type,
    raised by `corpus_root` — rather than `EquipmentMapError`. Both are
    `ValueError` subclasses and the console entry point catches both, so the
    caller's handling is unaffected; the distinction is recorded here rather
    than papered over, because an empty or partially fetched checkout must fail
    loudly and it does.
    """
    with pytest.raises(CorpusPathError):
        real_layer_sections(tmp_path / "no-such-corpus-root")


def test_real_layer_sections_refuses_a_symlinked_path_to_the_real_manifest(
    tmp_path: Path,
) -> None:
    """VR-067's prohibition reaches this read too: a link's bytes live outside
    the repository and are not what a clone receives."""
    root = tmp_path / "corpus"
    elsewhere = tmp_path / "elsewhere" / "ufgs"
    elsewhere.mkdir(parents=True)
    (root / "real").mkdir(parents=True)
    try:
        (root / "real" / "ufgs").symlink_to(elsewhere, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform dependent
        pytest.skip(f"this platform does not permit creating symbolic links: {exc}")
    with pytest.raises(EquipmentMapError):
        real_layer_sections(root)


def test_real_layer_sections_refuses_a_missing_manifest(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    (root / "real" / "ufgs").mkdir(parents=True)
    with pytest.raises(EquipmentMapError):
        real_layer_sections(root)


def test_real_layer_sections_refuses_an_unparseable_manifest(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    (root / "real" / "ufgs").mkdir(parents=True)
    (root / "real" / "ufgs" / MANIFEST_FILENAME).write_bytes(b"{ not json\n")
    with pytest.raises(EquipmentMapError):
        real_layer_sections(root)


@pytest.mark.parametrize(
    "entries",
    [
        pytest.param([], id="no-entries"),
        pytest.param([{"location": "doc.pdf"}], id="no-section-recorded"),
    ],
)
def test_real_layer_sections_refuses_a_manifest_with_nothing_to_read(
    tmp_path: Path, entries: list[dict]
) -> None:
    """An empty population is a failure, not an empty answer: returning an empty
    set here would make every mapped section unbacked and report the defect
    under the wrong rule."""
    root = tmp_path / "corpus"
    _real_manifest(root, entries)
    with pytest.raises(EquipmentMapError):
        real_layer_sections(root)
