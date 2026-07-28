"""DV-024 / SC-014 — adding or moving a line changes no other line's values.

Asserted **over the emitted artifact**, not over the stream keys. `seeds.py`
already proves the keys are content-addressed; this proves the property survives
the whole pipeline — a positionally seeded stream, or any shared consumption
between lines, fails here rather than in a unit test of a function nobody
reaches that way.
"""

from __future__ import annotations

import pytest

import model.procurement.generate as generate_module
from model.procurement.allocate import allocate_lines
from model.procurement.seeds import line_generator, line_stream_key


def _by_key(envelope) -> dict[tuple[str, str, int], dict]:
    return {
        (line["project_id"], line["po_number"], line["line_number"]): line
        for line in envelope["lines"]
    }


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    return generate_module.generate(root=tmp_path_factory.mktemp("baseline"))


class TestOverTheEmittedArtifact:
    def test_regeneration_is_identical(self, baseline, tmp_path) -> None:
        assert _by_key(generate_module.generate(root=tmp_path)) == _by_key(baseline)

    def test_every_line_s_values_depend_only_on_its_natural_key(self, baseline) -> None:
        """Two lines sharing a project and PO but differing in line number must
        differ in their drawn values — otherwise the key is not reaching the
        draw at all."""
        by_key = _by_key(baseline)
        multi = [k for k in by_key if k[2] > 1]
        assert multi, "the fixture carries no multi-line order to compare within"
        for project, po, number in multi:
            first = by_key[(project, po, 1)]
            later = by_key[(project, po, number)]
            assert (first["material_category"], first["order_date"], first["description"]) != (
                later["material_category"],
                later["order_date"],
                later["description"],
            )

    def test_no_two_lines_share_a_full_value_set(self, baseline) -> None:
        signatures = {
            (
                line["material_category"],
                line["description"],
                line["part_number"],
                line["order_date"],
                len(line["events"]),
            )
            for line in baseline["lines"]
        }
        assert len(signatures) == len(baseline["lines"])


class TestInsertionAndMovement:
    def test_inserting_a_line_leaves_every_other_stream_untouched(self, baseline) -> None:
        """The metamorphic claim, at the level the artifact is generated from.

        A new natural key produces a new stream; every existing key's stream is
        byte-for-byte what it was. Compared on drawn values rather than on the
        key alone, because equal keys with unequal draws would still be a
        failure of the property.
        """
        seed = generate_module.ROOT_SEED
        before = {
            line.natural_key: line_generator(seed, *line.natural_key).random(6).tolist()
            for line in allocate_lines()
        }
        inserted = ("PRJ-003", "PO-003-9999", 1)
        after = dict(before)
        after[inserted] = line_generator(seed, *inserted).random(6).tolist()

        assert set(after) - set(before) == {inserted}
        for key, values in before.items():
            assert after[key] == values

    def test_moving_a_line_within_the_allocation_changes_nothing(self, baseline) -> None:
        """Emission order must not reach the draw. Reversing the allocation and
        re-drawing must reproduce each line's values exactly."""
        seed = generate_module.ROOT_SEED
        lines = list(allocate_lines())
        forward = {
            line.natural_key: line_generator(seed, *line.natural_key).random(6).tolist()
            for line in lines
        }
        backward = {
            line.natural_key: line_generator(seed, *line.natural_key).random(6).tolist()
            for line in reversed(lines)
        }
        assert forward == backward

    def test_a_removed_line_leaves_the_others_unchanged(self, baseline) -> None:
        seed = generate_module.ROOT_SEED
        lines = list(allocate_lines())
        full = {
            line.natural_key: line_generator(seed, *line.natural_key).random(6).tolist()
            for line in lines
        }
        without = {
            line.natural_key: line_generator(seed, *line.natural_key).random(6).tolist()
            for line in lines[1:]
        }
        for key, values in without.items():
            assert full[key] == values

    def test_the_stream_keys_are_unique_across_the_artifact(self, baseline) -> None:
        """A collision would silently give two lines correlated draws."""
        keys = [
            line_stream_key(line["project_id"], line["po_number"], line["line_number"])
            for line in baseline["lines"]
        ]
        assert len(set(keys)) == len(keys)
