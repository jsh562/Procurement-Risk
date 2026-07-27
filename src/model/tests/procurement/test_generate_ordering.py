"""DV-023 / FR-020 — both total orders hold, and two runs at one seed agree.

The three ways output reorders between runs at one seed — iterating a set, a
mapping keyed by a hash-randomised string, or a parallel work queue — each leave
every individual value correct. That is what makes them worth a dedicated check:
nothing about the data looks wrong afterwards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from model.procurement import paths
from model.procurement.generate import generate
from model.procurement.serialize import dataset_content_hash


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory) -> tuple[Path, Path]:
    first = tmp_path_factory.mktemp("run-a")
    second = tmp_path_factory.mktemp("run-b")
    generate(root=first)
    generate(root=second)
    return first, second


class TestStatedTotalOrders:
    def test_lines_are_ordered_by_natural_key(self, two_runs) -> None:
        root, _ = two_runs
        from model.procurement.serialize import read_payload

        lines = read_payload(paths.fixture_path(root))["lines"]
        keys = [(x["project_id"], x["po_number"], x["line_number"]) for x in lines]
        assert keys == sorted(keys)

    def test_events_are_ordered_by_sequence_no(self, two_runs) -> None:
        root, _ = two_runs
        from model.procurement.serialize import read_payload

        for line in read_payload(paths.fixture_path(root))["lines"]:
            numbers = [event["sequence_no"] for event in line["events"]]
            assert numbers == sorted(numbers)
            assert numbers == list(range(1, len(numbers) + 1))

    def test_occurred_at_increases_with_sequence_no(self, two_runs) -> None:
        root, _ = two_runs
        from model.procurement.serialize import read_payload

        for line in read_payload(paths.fixture_path(root))["lines"]:
            stamps = [event["occurred_at"] for event in line["events"]]
            assert stamps == sorted(stamps)
            assert len(set(stamps)) == len(stamps)

    def test_generation_inputs_are_ordered_by_path(self, two_runs) -> None:
        root, _ = two_runs
        from model.procurement.serialize import read_payload

        entries = read_payload(paths.fixture_path(root))["generation_inputs"]
        assert [e["path"] for e in entries] == sorted(e["path"] for e in entries)


class TestByteIdentity:
    def test_two_runs_produce_identical_fixture_bytes(self, two_runs) -> None:
        a, b = two_runs
        assert paths.fixture_path(a).read_bytes() == paths.fixture_path(b).read_bytes()

    def test_two_runs_produce_identical_sidecars(self, two_runs) -> None:
        a, b = two_runs
        assert paths.hash_path(a).read_bytes() == paths.hash_path(b).read_bytes()

    def test_two_runs_produce_identical_truth_records(self, two_runs) -> None:
        """The float rounding in `write_record` exists for exactly this."""
        a, b = two_runs
        assert paths.truth_path(a).read_bytes() == paths.truth_path(b).read_bytes()

    def test_the_digest_is_stable_across_runs(self, tmp_path) -> None:
        first = generate(root=tmp_path / "one")
        second = generate(root=tmp_path / "two")
        assert dataset_content_hash(first) == dataset_content_hash(second)

    def test_no_carriage_return_reaches_any_artifact(self, two_runs) -> None:
        """`write_bytes`, never text mode: on Windows text mode would emit CRLF
        and the file would differ from the Linux runner's byte for byte."""
        root, _ = two_runs
        for artifact in (
            paths.fixture_path(root),
            paths.hash_path(root),
            paths.truth_path(root),
        ):
            assert b"\r" not in artifact.read_bytes()


class TestNoHashOrderedIterationReachesTheWritePath:
    def test_the_write_path_iterates_no_set_literal(self) -> None:
        """A source-level check, because the failure is invisible in one run.

        Set iteration order is stable within a process and varies between them
        under hash randomisation, so a single run — and a single test run —
        cannot distinguish it from a correct total order.
        """
        import model.procurement.generate as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        write_path = source[source.index("def _write(") :]
        assert "in set(" not in write_path
        assert "in {" not in write_path
        assert ".keys()" not in write_path
        assert ".values()" not in write_path

    def test_the_envelope_builder_sorts_every_collection_it_emits(self) -> None:
        """`lines` and `generation_inputs` are the two ordered collections in the
        envelope, and both must be sorted where they are built rather than
        happening to come out ordered."""
        import model.procurement.generate as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        builder = source[source.index("envelope = {") : source.index("digest = dataset_content")]
        assert "sorted(" in builder
        assert "key=lambda pair: pair[0].allocated.natural_key" in builder
        assert 'sorted(entries, key=lambda entry: entry["path"])' in source

    def test_sorted_is_used_wherever_a_mapping_is_iterated(self) -> None:
        import model.procurement.truth as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for fragment in ("vendor_offsets.items()", "TIER_OFFSETS.items()"):
            assert f"sorted({fragment})" in source
