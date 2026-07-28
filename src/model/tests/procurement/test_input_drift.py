"""NC-4's unit half — each of the three inputs has its own failing case (DV-016).

"Each of the three" is the load-bearing part. A drift check covering two inputs
passes every test that happens to mutate one of the two, and the third can rot
indefinitely while the suite stays green.
"""

from __future__ import annotations

import pytest

from model.corpus.equipment import EQUIPMENT_MAP_INPUT_PATH
from model.corpus.manufacturers import MANUFACTURER_CATALOG_INPUT_PATH
from model.procurement import paths
from model.procurement.model import DIGEST_KIND_CANONICAL_CONTENT, DIGEST_KIND_RAW_BYTES
from model.procurement.serialize import read_payload
from model.procurement.validate import (
    ROSTER_INPUT_PATH,
    ValidationError,
    check_input_drift,
)

INPUT_PATHS = (ROSTER_INPUT_PATH, EQUIPMENT_MAP_INPUT_PATH, MANUFACTURER_CATALOG_INPUT_PATH)


@pytest.fixture
def envelope():
    return read_payload(paths.fixture_path())


def _mutate(envelope, path: str):
    for entry in envelope["generation_inputs"]:
        if entry["path"] == path:
            entry["digest"] = "sha256:" + "9" * 64
            return entry
    raise AssertionError(f"{path} is not a recorded generation input")


class TestEachInputHasItsOwnFailingCase:
    @pytest.mark.parametrize("path", INPUT_PATHS)
    def test_a_drifted_digest_refuses_and_names_the_input(self, envelope, path: str) -> None:
        _mutate(envelope, path)
        with pytest.raises(ValidationError) as raised:
            check_input_drift(envelope)
        assert path in str(raised.value)
        assert "drifted" in str(raised.value)

    def test_all_three_inputs_are_recorded(self, envelope) -> None:
        assert {e["path"] for e in envelope["generation_inputs"]} == set(INPUT_PATHS)

    def test_the_unmutated_envelope_passes(self, envelope) -> None:
        assert check_input_drift(envelope) == 3


class TestEachInputIsRecomputedUnderItsOwnConvention:
    """AD-010's per-owner rule. Recomputing one input under the other's
    convention reports a false mismatch on a file nobody touched."""

    def test_the_roster_is_canonical_content(self, envelope) -> None:
        entry = next(e for e in envelope["generation_inputs"] if e["path"] == ROSTER_INPUT_PATH)
        assert entry["digest_kind"] == DIGEST_KIND_CANONICAL_CONTENT

    @pytest.mark.parametrize("path", [EQUIPMENT_MAP_INPUT_PATH, MANUFACTURER_CATALOG_INPUT_PATH])
    def test_the_corpus_inputs_are_raw_bytes(self, envelope, path: str) -> None:
        entry = next(e for e in envelope["generation_inputs"] if e["path"] == path)
        assert entry["digest_kind"] == DIGEST_KIND_RAW_BYTES

    def test_both_conventions_are_actually_used(self, envelope) -> None:
        """A per-owner rule with one owner is not a rule."""
        kinds = {e["digest_kind"] for e in envelope["generation_inputs"]}
        assert kinds == {DIGEST_KIND_CANONICAL_CONTENT, DIGEST_KIND_RAW_BYTES}

    @pytest.mark.parametrize("path", INPUT_PATHS)
    def test_a_swapped_convention_is_refused(self, envelope, path: str) -> None:
        """Recorded under the wrong kind, the digest would be recomputed by the
        wrong function and report a mismatch on an untouched file. Refusing on
        the *kind* catches it before it becomes a confusing drift report."""
        entry = next(e for e in envelope["generation_inputs"] if e["path"] == path)
        entry["digest_kind"] = (
            DIGEST_KIND_RAW_BYTES
            if entry["digest_kind"] == DIGEST_KIND_CANONICAL_CONTENT
            else DIGEST_KIND_CANONICAL_CONTENT
        )
        with pytest.raises(ValidationError, match="published as"):
            check_input_drift(envelope)


class TestDegenerateEnvelopes:
    def test_an_empty_input_list_is_refused(self, envelope) -> None:
        """Nothing binds the dataset to the files it came from."""
        envelope["generation_inputs"] = []
        with pytest.raises(ValidationError, match="no generation input"):
            check_input_drift(envelope)

    def test_an_unknown_input_is_refused(self, envelope) -> None:
        envelope["generation_inputs"].append(
            {
                "path": "data/corpus/synthetic/invented.json",
                "digest": "sha256:" + "3" * 64,
                "digest_kind": DIGEST_KIND_RAW_BYTES,
            }
        )
        with pytest.raises(ValidationError, match="no recomputation rule"):
            check_input_drift(envelope)

    def test_the_check_counts_what_the_envelope_records(self, envelope) -> None:
        """Driven by iteration, not by a literal — FR-027 was corrected because a
        count fixed at two survived the arrival of a third input."""
        assert check_input_drift(envelope) == len(envelope["generation_inputs"])
