"""DV-020 / SC-021 — exactly four artifacts, no split, no partition of `lines[]`.

FR-028 emits no train/evaluation split. The way that fails silently is not a file
called `train.json` — it is a second file that happens to hold a subset of the
lines, or a field on each line that a downstream reader treats as a split label.
Both are checked.
"""

from __future__ import annotations

import json

import pytest

from model.procurement import paths
from model.procurement.generate import generate
from model.procurement.serialize import read_payload

SPLIT_WORDS = ("split", "train", "test", "valid", "holdout", "fold", "partition")


@pytest.fixture(scope="module")
def emitted(tmp_path_factory):
    root = tmp_path_factory.mktemp("emitted")
    generate(root=root)
    return root


def test_exactly_four_artifacts_are_emitted(emitted) -> None:
    produced = sorted(p.relative_to(emitted).as_posix() for p in emitted.rglob("*") if p.is_file())
    assert len(produced) == 4, produced


def test_the_four_are_the_named_ones(emitted) -> None:
    expected = {
        paths.fixture_path(emitted),
        paths.hash_path(emitted),
        paths.datasheet_path(emitted),
        paths.truth_path(emitted),
    }
    assert {p for p in emitted.rglob("*") if p.is_file()} == expected


def test_the_ground_truth_record_sits_outside_the_dataset_tree(emitted) -> None:
    assert paths.procurement_dir(emitted) not in paths.truth_path(emitted).parents


def test_no_file_partitions_the_lines(emitted) -> None:
    """A second file holding a subset of `lines[]` would be a split by another
    name. Only one artifact carries lines at all."""
    carrying = []
    for path in emitted.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "lines" in payload:
            carrying.append(path)
    assert carrying == [paths.fixture_path(emitted)]


def test_no_line_carries_a_split_label(emitted) -> None:
    for line in read_payload(paths.fixture_path(emitted))["lines"]:
        for key in line:
            assert not any(word in key.lower() for word in SPLIT_WORDS)


def test_no_envelope_key_is_a_split_label(emitted) -> None:
    for key in read_payload(paths.fixture_path(emitted)):
        assert not any(word in key.lower() for word in SPLIT_WORDS)


def test_no_artifact_filename_suggests_a_split(emitted) -> None:
    for path in emitted.rglob("*"):
        if path.is_file():
            assert not any(word in path.name.lower() for word in SPLIT_WORDS)


def test_the_datasheet_states_that_no_split_is_emitted(emitted) -> None:
    text = paths.datasheet_path(emitted).read_text(encoding="utf-8")
    assert "No train/evaluation split is emitted" in text
    assert "unassigned" in text
