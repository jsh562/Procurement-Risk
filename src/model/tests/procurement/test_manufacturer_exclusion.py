"""DV-021 — no drawn manufacturer is a real firm, or a roster vendor.

FR-037 states this obligation **directly** rather than inheriting it from Scope.
Scope's inheritance covers projects and vendors, whose identities come from the
roster; manufacturer names come from E002's catalog, which the roster's
exclusion machinery never sees.

The check runs over **drawn** values. It originally verified a name space this
epic constructed; it now verifies one another epic publishes, which is the case
where it is actually load-bearing — a catalog we did not write is not
self-evidently free of real firms.
"""

from __future__ import annotations

import re

import numpy as np

from model.corpus.manufacturers import MANUFACTURERS
from model.procurement.durations import TIER_OFFSETS
from model.procurement.equipment import alias_spellings, draw_equipment
from model.roster.naming import load_convention, load_exclusions, normalize
from model.roster.reader import read_roster

SEED = 20260727
CATEGORIES = sorted(TIER_OFFSETS)


def _drawn_manufacturers() -> set[str]:
    rng = np.random.default_rng(SEED)
    return {
        line.manufacturer
        for i in range(600)
        if (line := draw_equipment(rng, CATEGORIES[i % len(CATEGORIES)], True)).manufacturer
    }


class TestRealFirmExclusion:
    def test_the_exclusion_list_is_non_empty(self) -> None:
        """A vacuous pass over an empty list would satisfy every assertion below
        while checking nothing."""
        assert load_exclusions()

    def test_no_drawn_manufacturer_normalizes_into_the_exclusion_list(self) -> None:
        convention = load_convention()
        excluded = {normalize(name, convention) for name in load_exclusions()}
        for name in _drawn_manufacturers():
            assert normalize(name, convention) not in excluded

    def test_the_whole_catalog_is_clean_not_just_the_drawn_subset(self) -> None:
        """Stronger than the requirement, and cheap: a name that is excluded but
        happens not to be drawn at this seed is still a defect waiting for a
        different seed."""
        convention = load_convention()
        excluded = {normalize(name, convention) for name in load_exclusions()}
        for entry in MANUFACTURERS.values():
            assert normalize(entry.canonical_name, convention) not in excluded

    def test_no_alias_spelling_is_excluded_either(self) -> None:
        """E006 collapses aliases onto canonical names. An excluded alias would
        put a real firm's spelling in the corpus even though E005 never emits it."""
        convention = load_convention()
        excluded = {normalize(name, convention) for name in load_exclusions()}
        for entry in MANUFACTURERS.values():
            for spelling in alias_spellings(entry.canonical_name):
                assert normalize(spelling, convention) not in excluded

    def test_the_normalizer_actually_catches_a_planted_match(self) -> None:
        """The check is only meaningful if the comparison can fire. Plant a
        known-excluded name and confirm the same code path rejects it."""
        convention = load_convention()
        excluded = {normalize(name, convention) for name in load_exclusions()}
        planted = load_exclusions()[0]
        assert normalize(planted.upper(), convention) in excluded


class TestVendorConventionExclusion:
    def test_no_drawn_manufacturer_matches_the_vendor_name_pattern(self) -> None:
        """A manufacturer that parsed as a roster vendor would make the two
        entity kinds indistinguishable in the corpus."""
        pattern = re.compile(load_convention()["vendors"]["name_pattern"])
        for name in _drawn_manufacturers():
            assert not pattern.fullmatch(name)

    def test_no_drawn_manufacturer_equals_a_roster_vendor_name(self) -> None:
        convention = load_convention()
        vendor_names = {normalize(v.name, convention) for v in read_roster().vendors}
        for name in _drawn_manufacturers():
            assert normalize(name, convention) not in vendor_names

    def test_no_drawn_manufacturer_equals_a_roster_project_name(self) -> None:
        convention = load_convention()
        project_names = {normalize(p.name, convention) for p in read_roster().projects}
        for name in _drawn_manufacturers():
            assert normalize(name, convention) not in project_names


def test_the_draw_covers_the_whole_catalog() -> None:
    """Otherwise the assertions above are about a subset and the untested
    remainder is exactly where a bad name would sit."""
    assert _drawn_manufacturers() == {e.canonical_name for e in MANUFACTURERS.values()}
