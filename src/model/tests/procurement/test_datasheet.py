"""SC-015 / SC-018 — every assumption recoverable from the datasheet alone.

"Alone" is the operative word. A reader with only this file must be able to
recover the duration model, the criticality mapping and the full provenance
without opening the generator's source — otherwise the datasheet documents that
a model exists rather than what it is.
"""

from __future__ import annotations

import pytest

from model.procurement import paths
from model.procurement.criticality import BAND_TABLE, PRESSURE_LEVELS, SLACK_MEAN, TIERS
from model.procurement.datasheet import ACTIVE_LIMITATIONS, SECTION_TITLES, WITHDRAWN_LIMITATIONS
from model.procurement.durations import (
    FORWARD_SHARES,
    LOOP_SHARES,
    SIGMA_0,
    T_PRE,
    TIER_OFFSETS,
)
from model.procurement.serialize import read_payload
from model.procurement.validate import check_datasheet


@pytest.fixture(scope="module")
def text() -> str:
    return paths.datasheet_path().read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def envelope():
    return read_payload(paths.fixture_path())


class TestSections:
    def test_all_seven_sections_are_present_in_order(self, text: str) -> None:
        positions = [text.index(f". {title}") for title in SECTION_TITLES]
        assert positions == sorted(positions)

    def test_the_conformance_check_passes(self) -> None:
        assert check_datasheet() == len(ACTIVE_LIMITATIONS)


class TestProvenanceIsRecoverable:
    def test_the_seed_and_scheme_are_published(self, text: str, envelope) -> None:
        assert str(envelope["root_seed"]) in text
        assert "spawn_key" in text

    def test_all_three_input_digests_are_named_with_their_kind(self, text: str, envelope) -> None:
        for entry in envelope["generation_inputs"]:
            assert entry["path"] in text
            assert entry["digest"] in text
            assert entry["digest_kind"] in text

    def test_the_generation_date_is_a_committed_constant(self, text: str, envelope) -> None:
        assert envelope["generation_date"] in text
        assert "never the run date" in text

    def test_the_layer_label_is_published(self, text: str) -> None:
        assert "SYNTHETIC" in text


class TestDurationModelIsRecoverable:
    def test_the_family_and_scale_are_published(self, text: str) -> None:
        assert "lognormal" in text.lower()
        assert str(SIGMA_0) in text

    def test_the_parameterization_is_the_generator_s_own(self, text: str) -> None:
        """Not a re-expressed one — a reader must be able to reproduce the draw."""
        assert "ln(share × T_pre) − σ₀²/2" in text
        assert str(T_PRE) in text

    def test_the_apportionment_is_published(self, text: str) -> None:
        assert str(FORWARD_SHARES) in text
        assert str(LOOP_SHARES) in text

    def test_the_rounding_rule_and_floor_are_published(self, text: str) -> None:
        assert "whole days" in text
        assert "1 day" in text
        assert "occurred_at" in text

    def test_the_three_transition_loop_is_stated(self, text: str) -> None:
        """The defect that reached the end-to-end run. Stating it is what stops a
        reader re-deriving the two-leg version."""
        assert "three transitions per loop, not two" in text


class TestCriticalityMappingIsRecoverable:
    def test_every_tier_and_pressure_level_appears(self, text: str) -> None:
        for tier in TIERS:
            assert f"**{tier}**" in text
        for level in PRESSURE_LEVELS:
            assert level in text

    def test_the_full_nine_cell_table_is_published(self, text: str) -> None:
        table = text[text.index("| Tier \\ Pressure") :]
        for tier in TIERS:
            row = table[table.index(f"| **{tier}** |") :].split("\n")[0]
            for level in PRESSURE_LEVELS:
                assert str(BAND_TABLE[(tier, level)]) in row

    def test_every_category_is_assigned_a_tier(self, text: str) -> None:
        for category in TIER_OFFSETS:
            assert f"`{category}`" in text

    def test_the_two_duration_quantities_are_named_distinctly(self, text: str) -> None:
        """SC-028 / FR-035: they differ by a factor a reader cannot see."""
        assert "category_expected_duration_days" in text
        assert "line_expected_total_duration_days" in text
        assert "never be written where the other is meant" in text

    def test_the_derivation_direction_is_stated(self, text: str) -> None:
        assert "derived" in text and "drawn" in text
        assert str(SLACK_MEAN) in text

    def test_the_tercile_cut_points_are_published(self, text: str) -> None:
        assert "Tercile cut points" in text


class TestLimitations:
    def test_nine_active_records(self) -> None:
        assert len(ACTIVE_LIMITATIONS) == 9

    def test_every_record_carries_all_four_parts(self, text: str) -> None:
        for label in (
            "Scope decision",
            "Supporting evidence",
            "Reversal trigger",
            "Production-scale alternative",
        ):
            assert text.count(f"**{label}**") == len(ACTIVE_LIMITATIONS)

    def test_the_withdrawn_record_is_not_emitted_as_active(self, text: str) -> None:
        for identifier in WITHDRAWN_LIMITATIONS:
            assert f"### {identifier} —" not in text

    def test_the_withdrawal_is_disclosed_rather_than_silent(self, text: str) -> None:
        assert "withdrawn" in text.lower()
        for identifier in WITHDRAWN_LIMITATIONS:
            assert identifier in text

    def test_the_identifiers_are_not_renumbered(self) -> None:
        """L-6…L-10 must keep meaning what other artifacts say they mean."""
        identifiers = [record.identifier for record in ACTIVE_LIMITATIONS]
        assert identifiers == ["L-1", "L-2", "L-3", "L-4", "L-6", "L-7", "L-8", "L-9", "L-10"]

    def test_a_020_is_disclosed_in_l_4(self, text: str) -> None:
        """The carried-open finding reaches the datasheet's reader rather than
        living only in an internal report."""
        assert "carries no category term" in text
        assert "A-020" in text


class TestSplitDisclosure:
    def test_no_split_is_emitted_and_the_owning_epics_are_named(self, text: str) -> None:
        assert "No train/evaluation split is emitted" in text
        # Was "Ownership of the split is unassigned" until 2026-07-28. FR-028 and
        # SC-021 now require the datasheet to name who owns the split, because
        # `specs/project-plan.md` assigns it and the old sentence contradicted the
        # canonical source.
        assert "constructed by E007 and frozen and hashed by E014" in text

    def test_the_assumed_fraction_is_labelled_as_assumed(self, text: str) -> None:
        assert "0.25" in text
        assert "assumed cross-epic fraction" in text

    def test_no_split_label_appears_anywhere(self, envelope) -> None:
        raw = paths.fixture_path().read_text(encoding="utf-8")
        for label in ('"split"', '"train"', '"test"', '"validation"', '"holdout"'):
            assert label not in raw


class TestDeterminism:
    def test_the_datasheet_reads_no_clock(self) -> None:
        from pathlib import Path

        import model.procurement.datasheet as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("datetime.now", "date.today", "time.time", "utcnow"):
            assert forbidden not in source

    def test_regeneration_produces_identical_bytes(self, tmp_path) -> None:
        from model.procurement.generate import generate

        generate(root=tmp_path / "a")
        generate(root=tmp_path / "b")
        assert (
            paths.datasheet_path(tmp_path / "a").read_bytes()
            == paths.datasheet_path(tmp_path / "b").read_bytes()
        )
