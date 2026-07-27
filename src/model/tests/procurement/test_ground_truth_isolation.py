"""DV-018 / DV-026 / NC-2, NC-3 — the ground-truth record is out of reach.

The record publishes the vendor offsets the dataset was generated from. If a
model could see it during fitting, "the model recovered the vendor effect" would
be unfalsifiable — which is the whole reason the record exists.

**Input roots are enumerated from configuration, never listed here.** A test
carrying its own list of roots agrees with itself: it would keep passing after
someone added a root that contains the record. The roots come from
`model.procurement.paths`, which is also what the generator writes through, so
the two cannot disagree.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from model.procurement import paths


def enumerated_input_roots(root: Path | None = None) -> tuple[Path, ...]:
    """Every directory a fitting entry point would read the dataset from.

    Derived from `PROCUREMENT_DIR_PARTS`, the same constant `fixture_path`
    resolves through. E014 owns the fitting entry itself and does not exist yet;
    when it does, its configured roots replace this derivation rather than
    supplementing it — but the property being asserted is unchanged, and
    asserting it now is what stops the record drifting into a root later.
    """
    return (paths.procurement_dir(root),)


class TestDV026NonVacuous:
    """NC-3: an empty enumeration must fail rather than satisfy DV-018 vacuously."""

    def test_the_root_set_is_non_empty(self) -> None:
        assert enumerated_input_roots()

    def test_the_root_set_contains_the_dataset_directory(self) -> None:
        """Otherwise the enumeration is not describing the fitting inputs at all,
        and 'the record is outside every root' would be true of a root set that
        excludes the data too."""
        assert paths.fixture_path().parent in enumerated_input_roots()

    def test_an_empty_enumeration_would_fail_the_check(self) -> None:
        """The control on the control. If `record_is_isolated` returned True for
        an empty root set, every assertion in this file would pass on a machine
        where the enumeration silently broke."""
        assert not record_is_isolated((), paths.truth_path())


def record_is_isolated(roots: tuple[Path, ...], record: Path) -> bool:
    """True when `record` lies outside every root, and there is at least one."""
    if not roots:
        return False
    resolved = record.resolve()
    return not any(resolved.is_relative_to(root.resolve()) for root in roots)


class TestDV018Isolation:
    def test_the_record_is_outside_every_enumerated_root(self) -> None:
        assert record_is_isolated(enumerated_input_roots(), paths.truth_path())

    def test_the_two_trees_are_siblings_not_nested(self) -> None:
        dataset = paths.procurement_dir().resolve()
        truth = paths.ground_truth_dir().resolve()
        assert dataset != truth
        assert not truth.is_relative_to(dataset)
        assert not dataset.is_relative_to(truth)

    def test_the_fixture_is_inside_a_root(self) -> None:
        """The mirror image: the data a model *should* see is reachable."""
        assert not record_is_isolated(enumerated_input_roots(), paths.fixture_path())


class TestNC2ProbeCopy:
    """A probe copy inside an enumerated root must make the check fail."""

    def test_a_probe_inside_a_root_is_detected(self, tmp_path) -> None:
        root = tmp_path / "repo"
        (root / "data" / "procurement").mkdir(parents=True)
        (root / "data" / "ground-truth").mkdir(parents=True)
        shutil.copy(paths.truth_path(), paths.truth_path(root))

        assert record_is_isolated(enumerated_input_roots(root), paths.truth_path(root))

        probe = paths.procurement_dir(root) / "vendor-offsets.json"
        shutil.copy(paths.truth_path(), probe)
        assert not record_is_isolated(enumerated_input_roots(root), probe)

    def test_a_probe_under_any_name_is_detected(self, tmp_path) -> None:
        """The check is on location, not on filename — renaming the copy must
        not evade it, because a fitting entry reads a directory."""
        root = tmp_path / "repo"
        (root / "data" / "procurement").mkdir(parents=True)
        probe = paths.procurement_dir(root) / "innocuous.json"
        shutil.copy(paths.truth_path(), probe)
        assert not record_is_isolated(enumerated_input_roots(root), probe)

    def test_a_probe_in_a_nested_directory_is_detected(self, tmp_path) -> None:
        root = tmp_path / "repo"
        nested = paths.procurement_dir(root) / "nested" / "deeper"
        nested.mkdir(parents=True)
        probe = nested / "vendor-offsets.json"
        shutil.copy(paths.truth_path(), probe)
        assert not record_is_isolated(enumerated_input_roots(root), probe)


class TestTheRecordIsNotDiscoverableFromTheDataset:
    def test_no_emitted_dataset_artifact_names_the_record(self) -> None:
        for artifact in (paths.fixture_path(), paths.hash_path()):
            text = artifact.read_text(encoding="utf-8")
            assert paths.TRUTH_FILENAME not in text
            assert "ground-truth" not in text

    def test_the_datasheet_may_name_it_but_carries_no_offset(self) -> None:
        """The datasheet documents that the record exists — that is disclosure,
        not leakage. What it must not do is publish the values."""
        import json

        record = json.loads(paths.truth_path().read_text(encoding="utf-8"))
        text = paths.datasheet_path().read_text(encoding="utf-8")
        for entry in record["vendor_offsets"]:
            assert str(entry["offset_log"]) not in text

    def test_the_record_binds_to_the_fixture_one_way_only(self) -> None:
        """The record names the dataset; the dataset does not name the record.
        A back-reference would make the record reachable by following a link
        from inside an input root."""
        import json

        record = json.loads(paths.truth_path().read_text(encoding="utf-8"))
        assert record["dataset_content_hash"]
        fixture = paths.fixture_path().read_text(encoding="utf-8")
        assert "truth" not in fixture.lower()
