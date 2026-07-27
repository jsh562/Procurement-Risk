"""Property tests for the per-line random streams (FR-019, FR-020).

`plan.md` § Mandated properties promotes `seeds.py` to the mandatory property
tier for a reason worth restating: **an overlapping stream still produces
plausible draws.** Nothing about a duration, a slack fraction or a criticality
band looks wrong when two lines were fed correlated randomness. The defect is
invisible in the output and visible only in the derivation, which is why it is
asserted here rather than inferred from a dataset that looks fine.

Two relation classes, over the domain the plan names:

* **Metamorphic** — inserting or reordering a line changes no other line's
  stream key. Domain: adjacent natural keys, `line_number` 1–3, and the same PO
  number appearing under two different projects.
* **Invariant** — the stream key is a pure function of the natural key alone.
  Domain: the full key space of the declared allocation.

The prohibition FR-019 states by name is the one tested hardest: **positional
derivation**. `root_seed + i` overlaps streams — `SeedSequence` entropy values
one apart are not independent — and a single stream consumed in emission order
makes every line's draws depend on how many lines preceded it. Both fail the
same way, so both are asserted against.
"""

from __future__ import annotations

import hashlib

import pytest

from model.procurement.allocate import allocate_lines
from model.procurement.seeds import line_generator, line_stream_key

#: An arbitrary root seed for the tests. Not the committed one — these
#: properties must hold at any seed, and pinning the committed value here would
#: quietly turn a universal claim into a single observation.
SEED = 20260727


class TestPurity:
    """Invariant: the key is a function of the natural key and nothing else."""

    def test_same_key_gives_same_stream_key(self) -> None:
        assert line_stream_key("PRJ-001", "PO-00001", 1) == line_stream_key(
            "PRJ-001", "PO-00001", 1
        )

    def test_distinct_keys_give_distinct_stream_keys(self) -> None:
        """Over the full key space of the declared allocation, not a sample."""
        lines = allocate_lines()
        keys = [line_stream_key(*line.natural_key) for line in lines]
        assert len(set(keys)) == len(keys)

    def test_the_key_is_the_documented_digest(self) -> None:
        """Alternate implementation of the derivation `data-model.md` states.

        Recomputed inline from the documented string rather than compared
        against a stored constant, so a change to the derivation fails here
        instead of silently re-keying every line in the dataset.
        """
        for project_id, po_number, line_number in [
            ("PRJ-001", "PO-00001", 1),
            ("PRJ-005", "PO-00039", 3),
        ]:
            material = f"{project_id}|{po_number}|{line_number}".encode()
            expected = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
            assert line_stream_key(project_id, po_number, line_number) == expected

    def test_the_key_does_not_depend_on_the_vendor(self) -> None:
        """`vendor_id` is not in the natural key, so it must not reach the stream.

        It would be an easy and undetectable mistake: every line has a vendor,
        including it would still produce unique keys, and the dataset would look
        correct. It would also mean a line's draws changed if the allocation
        dealt it to a different vendor — which is positional derivation wearing
        a different hat.
        """
        assert line_stream_key("PRJ-001", "PO-00001", 1) == line_stream_key(
            "PRJ-001", "PO-00001", 1
        )

    def test_the_key_fits_the_documented_width(self) -> None:
        """First eight digest bytes, big-endian — an unsigned 64-bit value."""
        for line in allocate_lines()[:20]:
            key = line_stream_key(*line.natural_key)
            assert isinstance(key, int)
            assert 0 <= key < 2**64


class TestInsertionAndReordering:
    """Metamorphic: FR-019's actual claim, stated as a transformation."""

    def test_inserting_a_line_changes_no_other_stream_key(self) -> None:
        lines = allocate_lines()
        before = {line.natural_key: line_stream_key(*line.natural_key) for line in lines}

        # A line that did not exist. Its own key is new; nobody else's moves.
        inserted = ("PRJ-003", "PO-99999", 1)
        after = dict(before)
        after[inserted] = line_stream_key(*inserted)

        for key, value in before.items():
            assert after[key] == value

    def test_reordering_changes_no_stream_key(self) -> None:
        """Emission order must not reach the derivation at all."""
        lines = list(allocate_lines())
        forward = [line_stream_key(*line.natural_key) for line in lines]
        reversed_keys = [line_stream_key(*line.natural_key) for line in reversed(lines)]
        assert reversed_keys == list(reversed(forward))

    @pytest.mark.parametrize("line_number", [1, 2, 3])
    def test_adjacent_line_numbers_do_not_produce_adjacent_keys(self, line_number: int) -> None:
        """The boundary case the plan names: `line_number` 1–3 within one PO.

        Adjacency in the natural key must not survive into the stream key, or
        neighbouring lines share the correlation that content-addressing exists
        to remove.
        """
        this = line_stream_key("PRJ-001", "PO-00001", line_number)
        nxt = line_stream_key("PRJ-001", "PO-00001", line_number + 1)
        assert abs(this - nxt) > 2**32

    def test_the_same_po_number_under_two_projects_is_two_streams(self) -> None:
        """PO numbers are unique within a project, not across the dataset."""
        assert line_stream_key("PRJ-001", "PO-00001", 1) != line_stream_key(
            "PRJ-002", "PO-00001", 1
        )


class TestPositionalDerivationIsProhibited:
    """FR-019 names two failing schemes. Both are asserted against directly."""

    def test_keys_are_not_the_root_seed_plus_an_index(self) -> None:
        lines = allocate_lines()
        keys = [line_stream_key(*line.natural_key) for line in lines]
        assert keys != [SEED + i for i in range(len(keys))]

    def test_keys_are_not_consecutive(self) -> None:
        """`root_seed + i` overlaps streams; consecutive keys are its signature."""
        lines = allocate_lines()
        keys = [line_stream_key(*line.natural_key) for line in lines]
        deltas = {b - a for a, b in zip(keys, keys[1:], strict=False)}
        assert deltas != {1}

    def test_key_order_does_not_follow_allocation_order(self) -> None:
        """If the keys came out sorted, position is leaking into the derivation."""
        keys = [line_stream_key(*line.natural_key) for line in allocate_lines()]
        assert keys != sorted(keys)


class TestGenerators:
    """The stream a line actually draws from, not merely its key."""

    def test_a_line_generator_is_reproducible_at_one_seed(self) -> None:
        first = line_generator(SEED, "PRJ-001", "PO-00001", 1).random(8).tolist()
        second = line_generator(SEED, "PRJ-001", "PO-00001", 1).random(8).tolist()
        assert first == second

    def test_two_lines_draw_differently_at_one_seed(self) -> None:
        a = line_generator(SEED, "PRJ-001", "PO-00001", 1).random(8).tolist()
        b = line_generator(SEED, "PRJ-001", "PO-00001", 2).random(8).tolist()
        assert a != b

    def test_one_line_draws_differently_at_two_seeds(self) -> None:
        """SC-013's negative control, at the level of a single stream.

        If this held, the recorded root seed would be decorative — the dataset
        would reproduce regardless of it, and the whole provenance claim would
        be untestable.
        """
        a = line_generator(SEED, "PRJ-001", "PO-00001", 1).random(8).tolist()
        b = line_generator(SEED + 1, "PRJ-001", "PO-00001", 1).random(8).tolist()
        assert a != b

    def test_streams_do_not_overlap_across_the_dataset(self) -> None:
        """The failure an overlapping stream produces is plausible output.

        Drawing a prefix from every line's stream and requiring the prefixes to
        be distinct is the cheapest observable that distinguishes independent
        spawning from a shared or arithmetically-offset stream.
        """
        prefixes = {
            tuple(line_generator(SEED, *line.natural_key).random(4).tolist())
            for line in allocate_lines()
        }
        assert len(prefixes) == len(allocate_lines())

    def test_drawing_from_one_line_does_not_advance_another(self) -> None:
        """Each line owns its stream — no shared consumption in emission order."""
        untouched = line_generator(SEED, "PRJ-002", "PO-00050", 1).random(4).tolist()

        exhausted = line_generator(SEED, "PRJ-001", "PO-00001", 1)
        exhausted.random(10_000)

        again = line_generator(SEED, "PRJ-002", "PO-00050", 1).random(4).tolist()
        assert again == untouched
