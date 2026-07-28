"""T077 / NC-12 — the corpus side of the join exists, **inverted**.

This check spent its whole life asserting the opposite. While FR-034 was gated it
asserted that E002's vocabulary published *neither* `manufacturer` nor
`part_number`, and failed when it gained them — so a blocked cross-epic
dependency could not become a permanent one unnoticed.

E002 published both on 2026-07-26. The trigger fired, the gate was discharged,
and the check now guards the other direction: withdrawing either field would
break FR-034 silently, because the overlap share would simply fall and the
generator would refuse with a number rather than a cause.
"""

from __future__ import annotations

import json

import pytest

from model.corpus.manufacturers import (
    MANUFACTURER_CATALOG_INPUT_PATH,
    MANUFACTURERS,
    manufacturers_for_category,
)
from model.procurement import paths
from model.procurement.durations import TIER_OFFSETS
from model.procurement.serialize import read_payload

VOCABULARY_PATH = "data/corpus/synthetic/field-label-vocabulary.json"
REQUIRED_FIELDS = ("manufacturer", "part_number")


@pytest.fixture(scope="module")
def vocabulary() -> str:
    return (paths.REPO_ROOT / VOCABULARY_PATH).read_text(encoding="utf-8")


class TestTheFieldsArePublished:
    @pytest.mark.parametrize("field", REQUIRED_FIELDS)
    def test_the_vocabulary_publishes_the_field(self, vocabulary: str, field: str) -> None:
        assert field in vocabulary

    def test_the_vocabulary_file_exists(self) -> None:
        assert (paths.REPO_ROOT / VOCABULARY_PATH).is_file()

    def test_the_catalog_is_a_recorded_generation_input(self) -> None:
        assert (paths.REPO_ROOT / MANUFACTURER_CATALOG_INPUT_PATH).is_file()
        recorded = {e["path"] for e in read_payload(paths.fixture_path())["generation_inputs"]}
        assert MANUFACTURER_CATALOG_INPUT_PATH in recorded


class TestTheCatalogSupportsEveryCategory:
    def test_every_committed_category_has_a_manufacturer(self) -> None:
        """`manufacturers_for_category` raises rather than returning empty, so a
        gap fails loudly — but it would fail during generation, long after the
        catalog changed. Checking it directly names the cause."""
        for category in sorted(TIER_OFFSETS):
            assert manufacturers_for_category(category)

    def test_every_category_also_has_a_non_maker(self) -> None:
        """The complement draws a category-MISMATCHED entry, so each category
        needs at least one manufacturer that does not make it. Without that the
        catalog-overlap share could not fall below its floor."""
        for category in sorted(TIER_OFFSETS):
            making = set(manufacturers_for_category(category))
            assert set(MANUFACTURERS) - making

    def test_the_catalog_publishes_part_number_prefixes(self) -> None:
        raw = json.loads(
            (paths.REPO_ROOT / MANUFACTURER_CATALOG_INPUT_PATH).read_text(encoding="utf-8")
        )
        for key, entry in raw["manufacturers"].items():
            assert entry["part_number_prefix"] == key


class TestTheInversionIsRecorded:
    def test_the_dataset_actually_uses_both_fields(self) -> None:
        """The strongest form: not merely that E002 publishes them, but that this
        dataset draws on them. A withdrawal would break a live requirement."""
        lines = read_payload(paths.fixture_path())["lines"]
        assert all(line["manufacturer"] for line in lines)
        assert all(line["part_number"] for line in lines)

    def test_the_realized_share_is_recorded(self) -> None:
        record = json.loads(paths.truth_path().read_text(encoding="utf-8"))
        assert record["realized_catalog_overlap_share"] >= 0.60
