"""End-to-end generation: three artifacts, DV-022, and the truth record's binding.

T036 and T031. Runs the real pipeline into a temporary root, so the committed
artifacts are never touched by a test.
"""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from model.procurement import paths
from model.procurement.censor import AS_OF_DATE, ORDER_DATE_WINDOW
from model.procurement.equipment import UNITS_OF_MEASURE
from model.procurement.generate import (
    DATASET_SCHEMA_VERSION,
    GENERATOR_ID,
    ROOT_SEED,
    generate,
)
from model.procurement.lifecycle import STATES
from model.procurement.serialize import dataset_content_hash
from model.procurement.truth import TruthRecordError, validate_truth_record
from model.roster.reader import read_roster

#: The six FR-031 descriptive columns.
DESCRIPTIVE = (
    "material_category",
    "description",
    "manufacturer",
    "part_number",
    "unit_of_measure",
    "quantity",
)


@pytest.fixture(scope="module")
def emitted(tmp_path_factory) -> tuple[Path, dict]:
    root = tmp_path_factory.mktemp("repo")
    envelope = generate(root=root)
    return root, envelope


class TestArtifactSet:
    def test_exactly_the_expected_artifacts_are_written(self, emitted) -> None:
        root, _ = emitted
        assert paths.fixture_path(root).is_file()
        assert paths.hash_path(root).is_file()
        assert paths.truth_path(root).is_file()

    def test_the_ground_truth_record_is_outside_the_procurement_tree(self, emitted) -> None:
        """AD-007: a separate tree, so no fitting input root reaches it."""
        root, _ = emitted
        assert paths.procurement_dir(root) not in paths.truth_path(root).parents

    def test_the_sidecar_digest_matches_the_fixture(self, emitted) -> None:
        root, envelope = emitted
        sidecar = json.loads(paths.hash_path(root).read_text(encoding="utf-8"))
        assert sidecar["dataset_content_hash"] == dataset_content_hash(envelope)
        assert sidecar["dataset_schema_version"] == DATASET_SCHEMA_VERSION

    def test_every_file_ends_with_exactly_one_newline(self, emitted) -> None:
        root, _ = emitted
        for path in (paths.fixture_path(root), paths.hash_path(root), paths.truth_path(root)):
            raw = path.read_bytes()
            assert raw.endswith(b"\n")
            assert not raw.endswith(b"\n\n")
            assert b"\r\n" not in raw


class TestEnvelope:
    def test_the_envelope_carries_thirteen_keys(self, emitted) -> None:
        _, envelope = emitted
        assert len(envelope) == 13

    def test_the_committed_constants_are_recorded(self, emitted) -> None:
        _, envelope = emitted
        assert envelope["root_seed"] == ROOT_SEED
        assert envelope["as_of_date"] == AS_OF_DATE.isoformat()
        assert envelope["order_date_window"]["first"] == ORDER_DATE_WINDOW.first.isoformat()
        assert envelope["layer"] == "SYNTHETIC"
        assert envelope["generator_id"] == GENERATOR_ID

    def test_all_three_generation_inputs_carry_their_digest_kind(self, emitted) -> None:
        _, envelope = emitted
        entries = envelope["generation_inputs"]
        assert len(entries) == 3
        assert [e["path"] for e in entries] == sorted(e["path"] for e in entries)
        for entry in entries:
            assert set(entry) == {"path", "digest", "digest_kind"}
            assert entry["digest"].startswith("sha256:")
            assert entry["digest_kind"] in {"canonical_content", "raw_bytes"}

    def test_the_two_digest_conventions_are_both_used(self, emitted) -> None:
        """AD-010's per-owner rule is only observable if both appear."""
        _, envelope = emitted
        kinds = {e["digest_kind"] for e in envelope["generation_inputs"]}
        assert kinds == {"canonical_content", "raw_bytes"}


class TestLines:
    def test_the_line_count_is_inside_the_declared_band(self, emitted) -> None:
        _, envelope = emitted
        assert 190 <= len(envelope["lines"]) <= 210

    def test_the_six_descriptive_columns_are_present(self, emitted) -> None:
        _, envelope = emitted
        for line in envelope["lines"]:
            for column in DESCRIPTIVE:
                assert column in line

    def test_non_blank_after_trimming_where_present(self, emitted) -> None:
        """DV-004. `manufacturer` and `part_number` are `NULL` on the
        non-overlapping complement by design, so the assertion is on the value
        when there is one, not on its presence."""
        _, envelope = emitted
        for line in envelope["lines"]:
            for column in DESCRIPTIVE:
                value = line[column]
                if value is not None:
                    assert str(value).strip(" \t\n\r\f")

    def test_note_is_absent_from_every_event(self, emitted) -> None:
        """DV-022. `note` is `NULL` on every E005 event, so recording it would
        create a second place for one fact to be wrong."""
        _, envelope = emitted
        for line in envelope["lines"]:
            for event in line["events"]:
                assert set(event) == {"sequence_no", "to_state", "occurred_at"}

    def test_quantity_is_a_fixed_scale_decimal_string(self, emitted) -> None:
        _, envelope = emitted
        for line in envelope["lines"]:
            assert isinstance(line["quantity"], str)
            assert Decimal(line["quantity"]) == Decimal(line["quantity"]).quantize(Decimal("0.1"))

    def test_units_come_from_the_five_permitted_values(self, emitted) -> None:
        _, envelope = emitted
        assert {line["unit_of_measure"] for line in envelope["lines"]} <= set(UNITS_OF_MEASURE)

    def test_identities_come_from_the_roster(self, emitted) -> None:
        _, envelope = emitted
        known = read_roster().identifiers()
        for line in envelope["lines"]:
            assert line["project_id"] in known
            assert line["vendor_id"] in known

    def test_every_state_is_a_declared_one(self, emitted) -> None:
        _, envelope = emitted
        seen = {e["to_state"] for line in envelope["lines"] for e in line["events"]}
        assert seen <= set(STATES)

    def test_all_five_criticality_bands_occur(self, emitted) -> None:
        _, envelope = emitted
        assert set(Counter(line["criticality"] for line in envelope["lines"])) == {1, 2, 3, 4, 5}

    def test_no_instant_exceeds_the_as_of_date(self, emitted) -> None:
        _, envelope = emitted
        for line in envelope["lines"]:
            for event in line["events"]:
                assert event["occurred_at"] <= f"{AS_OF_DATE.isoformat()}T00:00:00Z"

    def test_need_by_is_never_before_the_order_date(self, emitted) -> None:
        _, envelope = emitted
        for line in envelope["lines"]:
            assert line["need_by_date"] >= line["order_date"]


class TestTruthRecord:
    def test_it_binds_to_the_emitted_fixture(self, emitted) -> None:
        root, envelope = emitted
        record = json.loads(paths.truth_path(root).read_text(encoding="utf-8"))
        assert record["dataset_content_hash"] == dataset_content_hash(envelope)

    def test_it_covers_exactly_the_roster_vendors(self, emitted) -> None:
        """DV-017."""
        root, _ = emitted
        record = json.loads(paths.truth_path(root).read_text(encoding="utf-8"))
        validate_truth_record(record, [entry.id for entry in read_roster().vendors])

    def test_a_partial_offset_set_is_refused(self, emitted) -> None:
        root, _ = emitted
        record = json.loads(paths.truth_path(root).read_text(encoding="utf-8"))
        record["vendor_offsets"] = record["vendor_offsets"][:11]
        with pytest.raises(TruthRecordError, match="12"):
            validate_truth_record(record)

    def test_a_record_with_no_binding_is_refused(self, emitted) -> None:
        root, _ = emitted
        record = json.loads(paths.truth_path(root).read_text(encoding="utf-8"))
        record["dataset_content_hash"] = ""
        with pytest.raises(TruthRecordError, match="bind"):
            validate_truth_record(record)

    def test_both_ratios_are_recorded(self, emitted) -> None:
        """FR-036 requires the unadjusted one beside the adjusted one."""
        root, _ = emitted
        record = json.loads(paths.truth_path(root).read_text(encoding="utf-8"))
        assert record["spread_ratio"] != record["spread_ratio_unadjusted"]
        assert set(record["variance_decomposition"]) == {"vendor", "material_category", "residual"}

    def test_the_two_overlap_shares_are_recorded_separately(self, emitted) -> None:
        root, _ = emitted
        record = json.loads(paths.truth_path(root).read_text(encoding="utf-8"))
        assert record["realized_corpus_overlap_share"] >= 0.60
        assert record["realized_catalog_overlap_share"] >= 0.60

    def test_no_truth_value_leaks_into_the_fixture(self, emitted) -> None:
        """The isolation the record exists to preserve."""
        root, envelope = emitted
        record = json.loads(paths.truth_path(root).read_text(encoding="utf-8"))
        fixture_text = paths.fixture_path(root).read_text(encoding="utf-8")
        for entry in record["vendor_offsets"]:
            assert str(entry["offset_log"]) not in fixture_text
        assert str(record["spread_ratio"]) not in fixture_text
