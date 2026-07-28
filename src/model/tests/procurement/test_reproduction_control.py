"""NC-1 / SC-013 — the oracle can fail.

A reproduction check that passes no matter what is a checksum of nothing. If a
different `root_seed` produced the same digest, the recorded seed would be
decorative and the whole provenance claim untestable — so this file exists to
prove the oracle discriminates.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import model.procurement.generate as generate_module
from model.procurement import paths
from model.procurement.serialize import dataset_content_hash, read_payload
from model.procurement.validate import ValidationError, check_reproduction


@pytest.fixture(scope="module")
def committed() -> str:
    return read_payload(paths.hash_path())["dataset_content_hash"]


@pytest.fixture
def at_seed():
    """The digest of the payload built at an arbitrary seed.

    `build_envelope` rather than `generate`, because `generate` runs DV-010's
    shape gate and an arbitrary seed usually fails it — `revise_and_resubmit`
    holds a line only when the as-of cut lands inside one short leg. SC-013 asks
    whether a different seed yields a different *digest*, which is a question
    about the payload, not about whether that payload would be admissible.
    """

    def run(seed: int) -> str:
        envelope, _, _ = generate_module.build_envelope(seed)
        return dataset_content_hash(envelope)

    return run


class TestADifferentSeedYieldsADifferentDigest:
    @pytest.mark.parametrize("delta", [1, 2, 1000])
    def test_a_nearby_seed_differs(self, committed, at_seed, delta: int) -> None:
        """Nearby seeds, not distant ones. `root_seed + 1` is the case a
        positional derivation would get wrong while looking correct."""
        assert at_seed(generate_module.ROOT_SEED + delta) != committed

    def test_a_distant_seed_differs(self, committed, at_seed) -> None:
        assert at_seed(999_983) != committed

    def test_two_different_seeds_differ_from_each_other(self, at_seed) -> None:
        """Not merely from the committed digest — from each other, so a
        degenerate generator that ignored the seed entirely would fail here."""
        assert at_seed(11) != at_seed(12)

    def test_the_committed_seed_reproduces(self, committed, at_seed) -> None:
        """The other half of the control. Discrimination is only meaningful
        alongside agreement."""
        assert at_seed(generate_module.ROOT_SEED) == committed


class TestTheOracleRefusesRatherThanWarning:
    def test_a_tampered_sidecar_is_refused(self, tmp_path, monkeypatch) -> None:
        """A fixture and a sidecar that disagree means one was edited after the
        other, which the oracle must catch before it regenerates anything."""
        from model.procurement.serialize import write_payload

        root = tmp_path / "repo"
        generate_module.generate(root=root)
        sidecar = read_payload(paths.hash_path(root))
        sidecar["dataset_content_hash"] = "sha256:" + "0" * 64
        write_payload(paths.hash_path(root), sidecar)

        with pytest.raises(ValidationError, match="sidecar records"):
            check_reproduction(root=root)

    def test_a_tampered_fixture_is_refused(self, tmp_path) -> None:
        from model.procurement.serialize import write_payload

        root = tmp_path / "repo"
        envelope = generate_module.generate(root=root)
        envelope["lines"][3]["description"] = "hand edited"
        write_payload(paths.fixture_path(root), envelope)

        with pytest.raises(ValidationError, match="digests to"):
            check_reproduction(root=root)

    def test_an_untampered_pair_passes(self, tmp_path) -> None:
        root = tmp_path / "repo"
        generate_module.generate(root=root)
        assert check_reproduction(root=root).startswith("sha256:")


def test_the_seed_is_actually_recorded_in_the_artifact(committed) -> None:
    """SC-013 turns on the recorded seed being the one that ran. A fixture that
    recorded one seed and generated from another would reproduce for the person
    who wrote it and nobody else."""
    envelope = read_payload(paths.fixture_path())
    assert envelope["root_seed"] == generate_module.ROOT_SEED
    assert "spawn_key" in envelope["seed_derivation"]
    assert isinstance(Path(paths.fixture_path()), Path)
