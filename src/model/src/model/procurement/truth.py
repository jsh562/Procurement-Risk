"""The ground-truth record: the parameters the dataset was generated from.

Committed to a **separate tree** (AD-007) and bound to one fixture by
`dataset_content_hash`. It exists to make a later recovery claim falsifiable —
if the offsets a downstream model recovers can be compared against the offsets
that produced the data, "the model learned the vendor effect" stops being an
assertion.

Isolation is the whole point, so nothing here may reach a loaded column or a
fixture field. `paths.truth_path()` keeps it outside every fitting input root
and DV-018 enumerates those roots from the fitting entry point's own
configuration rather than from a list maintained here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from model.procurement.durations import (
    SIGMA_R,
    SIGMA_W,
    TAU,
    TIER_OFFSETS,
    SpreadDecomposition,
)
from model.procurement.serialize import write_record

__all__ = ["TRUTH_SCHEMA_VERSION", "TruthRecordError", "build_truth_record", "write_truth_record"]

TRUTH_SCHEMA_VERSION = 1


class TruthRecordError(ValueError):
    """Raised when the record would not bind to exactly one fixture."""


def build_truth_record(
    *,
    generator_id: str,
    generator_revision: int,
    root_seed: int,
    generation_date: date,
    dataset_content_hash: str,
    decomposition: SpreadDecomposition,
    vendor_offsets: Mapping[str, float],
    realized_corpus_overlap_share: float,
    realized_catalog_overlap_share: float,
) -> dict[str, Any]:
    """Assemble the record. Refuses rather than emitting a record that binds to
    nothing or covers fewer than the roster's twelve vendors (DV-017)."""
    if not dataset_content_hash:
        raise TruthRecordError(
            "a ground-truth record with no dataset_content_hash binds to no fixture, "
            "which makes every claim it supports unfalsifiable"
        )
    if len(vendor_offsets) != 12:
        raise TruthRecordError(
            f"expected exactly 12 vendor offsets, found {len(vendor_offsets)}; a partial "
            f"set would let a recovery claim be checked against some vendors and not others"
        )

    return {
        "truth_schema_version": TRUTH_SCHEMA_VERSION,
        "generator_id": generator_id,
        "generator_revision": generator_revision,
        "root_seed": root_seed,
        "generation_date": generation_date.isoformat(),
        "dataset_content_hash": dataset_content_hash,
        "within_vendor_spread_sd_log": SIGMA_W,
        "between_vendor_spread_sd_log": TAU,
        "residual_spread_sd_log": SIGMA_R,
        # The ratio asserted against FR-008's band is the category-adjusted one
        # (FR-036); the unadjusted one is recorded beside it so the gap is visible.
        "spread_ratio": decomposition.adjusted_ratio,
        "spread_ratio_unadjusted": decomposition.unadjusted_ratio,
        "variance_decomposition": {
            "vendor": decomposition.vendor_variance,
            "material_category": decomposition.category_variance,
            "residual": decomposition.residual_variance,
        },
        "vendor_offsets": [
            {"vendor_id": vendor_id, "offset_log": offset}
            for vendor_id, offset in sorted(vendor_offsets.items())
        ],
        "material_category_tier_offsets": {
            category: offset for category, offset in sorted(TIER_OFFSETS.items())
        },
        "realized_corpus_overlap_share": realized_corpus_overlap_share,
        "realized_catalog_overlap_share": realized_catalog_overlap_share,
    }


def write_truth_record(path: Path, record: Mapping[str, Any]) -> None:
    validate_truth_record(record)
    write_record(path, dict(record))


def validate_truth_record(record: Mapping[str, Any], roster_vendor_ids: Sequence[str] = ()) -> None:
    """DV-017: exactly twelve unique vendor ids, covering the roster."""
    offsets = record.get("vendor_offsets", [])
    ids = [entry["vendor_id"] for entry in offsets]
    if len(ids) != 12:
        raise TruthRecordError(f"expected 12 vendor offsets, found {len(ids)}")
    if len(set(ids)) != len(ids):
        raise TruthRecordError("vendor offsets repeat a vendor id")
    if roster_vendor_ids and set(ids) != set(roster_vendor_ids):
        missing = sorted(set(roster_vendor_ids) - set(ids))
        extra = sorted(set(ids) - set(roster_vendor_ids))
        raise TruthRecordError(
            f"vendor offsets do not cover the roster — missing {missing}, unexpected {extra}"
        )
    if not record.get("dataset_content_hash"):
        raise TruthRecordError("the record carries no dataset_content_hash to bind it")
