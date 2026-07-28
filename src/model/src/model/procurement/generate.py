"""`procurement-generate` — the whole pipeline, and the only writer of the fixture.

Ordering is stated, never incidental (FR-020): lines by
`(project_id, po_number, line_number)`, events by `sequence_no`. No set, no
hash-randomised mapping and no work queue reaches the write path, because each
of the three reorders output between runs at one seed while every individual
draw stays correct.

The shape gate runs **before** the write path, so a dataset below DV-010's
floors leaves no partial artifact behind for a later run to mistake for a
finished one.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from model.corpus.equipment import EQUIPMENT_MAP_INPUT_PATH, load_category_map
from model.corpus.manifest import sha256_of_file
from model.corpus.manufacturers import MANUFACTURER_CATALOG_INPUT_PATH
from model.procurement import paths
from model.procurement.allocate import (
    AllocatedLine,
    allocate_lines,
    rework_loop_allocation,
    shrinkage,
)
from model.procurement.censor import (
    AS_OF_DATE,
    ORDER_DATE_WINDOW,
    check_shape_floors,
    event_dates,
    is_delivered_by,
)
from model.procurement.criticality import (
    check_late_share,
    criticality_band,
    draw_slack_days,
    need_by_date,
    pressure_terciles,
    tercile_cut_points,
)
from model.procurement.durations import (
    TIER_OFFSETS,
    category_expected_duration_days,
    check_aggregate_duration,
    check_spread_band,
    decompose_spread,
    draw_line_durations,
    line_expected_total_duration_days,
    vendor_offsets,
)
from model.procurement.equipment import (
    OVERLAP_SHARE_FLOOR,
    catalog_overlap_share,
    corpus_overlap_share,
    draw_equipment,
)
from model.procurement.lifecycle import NON_TERMINAL_STATES, walk
from model.procurement.model import (
    DIGEST_KIND_CANONICAL_CONTENT,
    DIGEST_KIND_RAW_BYTES,
)
from model.procurement.seeds import line_generator
from model.procurement.serialize import dataset_content_hash, write_payload
from model.procurement.truth import build_truth_record, write_truth_record
from model.roster.reader import read_roster

__all__ = [
    "DATASET_SCHEMA_VERSION",
    "GENERATION_DATE",
    "GENERATOR_ID",
    "GENERATOR_REVISION",
    "ROOT_SEED",
    "SEED_DERIVATION",
    "GenerationError",
    "build_envelope",
    "generate",
    "main",
]

DATASET_SCHEMA_VERSION = 1
GENERATOR_ID = "model.procurement.generate"
GENERATOR_REVISION = 1

#: Committed, never read from a clock. A run-date default would move the content
#: hash the day after generation while the recorded seed still looked honoured.
#:
#: **Not selected to pass anything** — it is the generation date, and it is the
#: first value tried.
#:
#: It is worth recording why that sentence needed writing. This seed initially
#: failed FR-010's empty-non-terminal-state gate on `revise_and_resubmit`, and
#: the obvious reading was the one `data-model.md` had already predicted:
#: "`approved` and `revise_and_resubmit` are the thin ones", remedy "a new seed
#: or a widened window". A seed search found passing candidates within 17 tries
#: and the constant was changed to one of them.
#:
#: That was wrong. The state was empty because `draw_line_durations` emitted two
#: legs per rework loop against a state sequence that visits three, so every
#: rework line ran one event short and could never rest in the state the loop
#: passes through last. Once `LOOP_SHARES` fixed the leg count this seed passes
#: on its own: 177 delivered of 199 (0.889), every non-terminal state occupied.
#: A documented, plausible fragility was standing in front of a plain defect,
#: and the seed search would have buried it under a constant that "worked".
ROOT_SEED = 20260416
GENERATION_DATE = date(2026, 4, 16)

SEED_DERIVATION = (
    "SeedSequence(entropy=root_seed, spawn_key=(int.from_bytes("
    'sha256("<project_id>|<po_number>|<line_number>".encode("utf-8")).digest()[:8], "big"),))'
)

_ROSTER_INPUT_PATH = "data/roster/project-vendor-roster.json"

#: The share of lines drawn as corpus-overlapping. Above FR-032's 60% floor with
#: margin, because the realized share is *measured* — a target set exactly at the
#: floor would fail half the time on rounding alone.
_OVERLAP_TARGET_SHARE = 0.70


class GenerationError(RuntimeError):
    """Raised when the dataset cannot be emitted. Nothing is written."""


@dataclass(frozen=True, slots=True)
class _DrawnLine:
    allocated: AllocatedLine
    material_category: str
    description: str
    manufacturer: str | None
    part_number: str | None
    quantity: Decimal
    unit_of_measure: str
    order_date: date
    log_offset: float
    rework_loops: int
    leg_days: tuple[int, ...]
    line_expected_days: float
    slack_days: int
    delivered: bool
    events: tuple[tuple[int, str, date], ...]


def _generation_inputs() -> list[dict[str, str]]:
    """All three inputs, each hashed by the convention its owning epic publishes.

    Ordered by path so the envelope is stable, and built by iterating a list so a
    fourth input joins the drift check automatically (FR-027).

    Inputs are always read from the repository, **never from `generate`'s `root`
    argument**. That argument redirects where artifacts are *written* so a test
    can run the real pipeline without touching the committed tree; pointing the
    inputs at it too would make a temporary directory the provenance source and
    the digests meaningless.
    """
    base = paths.REPO_ROOT
    roster = read_roster()
    entries = [
        {
            "path": _ROSTER_INPUT_PATH,
            "digest": roster.content_hash,
            "digest_kind": DIGEST_KIND_CANONICAL_CONTENT,
        },
        {
            "path": EQUIPMENT_MAP_INPUT_PATH,
            "digest": sha256_of_file(base / EQUIPMENT_MAP_INPUT_PATH),
            "digest_kind": DIGEST_KIND_RAW_BYTES,
        },
        {
            "path": MANUFACTURER_CATALOG_INPUT_PATH,
            "digest": sha256_of_file(base / MANUFACTURER_CATALOG_INPUT_PATH),
            "digest_kind": DIGEST_KIND_RAW_BYTES,
        },
    ]
    return sorted(entries, key=lambda entry: entry["path"])


def _draw_lines(
    allocated: Sequence[AllocatedLine],
    offsets: Mapping[str, float],
    category_keys: Sequence[str],
) -> list[_DrawnLine]:
    """One pass over the allocation, each line drawing from its own stream."""
    loops = rework_loop_allocation(len(allocated))
    window_days = (ORDER_DATE_WINDOW.last - ORDER_DATE_WINDOW.first).days
    overlap_cut = round(_OVERLAP_TARGET_SHARE * len(allocated))

    drawn: list[_DrawnLine] = []
    for index, line in enumerate(allocated):
        generator = line_generator(ROOT_SEED, *line.natural_key)
        category = category_keys[int(generator.integers(0, len(category_keys)))]
        overlapping = index < overlap_cut

        equipment = draw_equipment(generator, category, overlapping)
        order_date = ORDER_DATE_WINDOW.first + _days(int(generator.integers(0, window_days + 1)))

        offset = offsets[line.vendor_id] + TIER_OFFSETS[category]
        rework = loops[index]
        legs = tuple(draw_line_durations(generator, offset, rework))

        expected = line_expected_total_duration_days(category, offsets[line.vendor_id])
        slack = draw_slack_days(generator, expected)

        dates = event_dates(order_date, legs, AS_OF_DATE)
        events = tuple(
            (event.sequence_no, event.to_state, event.occurred_at)
            for event in walk(order_date, dates, rework)
        )
        drawn.append(
            _DrawnLine(
                allocated=line,
                material_category=category,
                description=equipment.description,
                manufacturer=equipment.manufacturer,
                part_number=equipment.part_number,
                quantity=equipment.quantity,
                unit_of_measure=equipment.unit_of_measure,
                order_date=order_date,
                log_offset=offset,
                rework_loops=rework,
                leg_days=legs,
                line_expected_days=expected,
                slack_days=slack,
                delivered=is_delivered_by(order_date, legs, AS_OF_DATE),
                events=events,
            )
        )
    return drawn


def _days(count: int):
    from datetime import timedelta

    return timedelta(days=count)


def _line_payload(line: _DrawnLine, band: int) -> dict[str, Any]:
    return {
        "project_id": line.allocated.project_id,
        "vendor_id": line.allocated.vendor_id,
        "po_number": line.allocated.po_number,
        "line_number": line.allocated.line_number,
        "material_category": line.material_category,
        "description": line.description,
        "manufacturer": line.manufacturer,
        "part_number": line.part_number,
        "quantity": f"{line.quantity:f}",
        "unit_of_measure": line.unit_of_measure,
        "order_date": line.order_date.isoformat(),
        "need_by_date": need_by_date(
            line.order_date, line.line_expected_days, line.slack_days
        ).isoformat(),
        "criticality": band,
        # Events by `sequence_no`, which the walk already guarantees.
        "events": [
            {
                "sequence_no": sequence_no,
                "to_state": to_state,
                "occurred_at": f"{occurred_at.isoformat()}T00:00:00Z",
            }
            for sequence_no, to_state, occurred_at in line.events
        ],
    }


def build_envelope(seed: int | None = None) -> tuple[dict[str, Any], list, Mapping[str, float]]:
    """Draw the dataset and assemble the envelope. **No gates, no writes.**

    Split out of `generate` so a control can compare two seeds' digests without
    satisfying DV-010 — an arbitrary seed usually will not, and SC-013's question
    ("does a different seed produce a different digest?") is about the payload,
    not about whether that payload would be admissible.

    Returning the drawn lines alongside the envelope keeps the gates in
    `generate` reading the same objects rather than recomputing them.
    """
    global ROOT_SEED
    original = ROOT_SEED
    if seed is not None:
        ROOT_SEED = seed
    try:
        return _build(original if seed is None else seed)
    finally:
        ROOT_SEED = original


def _build(_seed: int) -> tuple[dict[str, Any], list, Mapping[str, float]]:
    roster = read_roster()
    category_keys = sorted(load_category_map())
    if set(category_keys) != set(TIER_OFFSETS):
        raise GenerationError(
            "the committed category map and the declared tier offsets disagree; a category "
            "with no tier has no expected duration and no criticality band"
        )

    allocated = allocate_lines()
    offsets = vendor_offsets(tuple(entry.id for entry in roster.vendors))
    drawn = _draw_lines(allocated, offsets, category_keys)

    # Pressure terciles are computed over the realized dataset as a whole, so the
    # bands cannot be assigned until every line has been drawn.
    ratios = [
        line.slack_days / category_expected_duration_days(line.material_category) for line in drawn
    ]
    bands = [
        criticality_band(line.material_category, level)
        for line, level in zip(drawn, pressure_terciles(ratios), strict=True)
    ]

    envelope = {
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "layer": "SYNTHETIC",
        "generator_id": GENERATOR_ID,
        "generator_revision": GENERATOR_REVISION,
        "root_seed": ROOT_SEED,
        "seed_derivation": SEED_DERIVATION,
        "generation_date": GENERATION_DATE.isoformat(),
        "as_of_date": AS_OF_DATE.isoformat(),
        "order_date_window": {
            "first": ORDER_DATE_WINDOW.first.isoformat(),
            "last": ORDER_DATE_WINDOW.last.isoformat(),
        },
        "generation_inputs": _generation_inputs(),
        "library_pin": {"numpy": np.__version__},
        "license_basis": {
            "basis_id": "SYNTHETIC-GENERATED",
            "generated_by_this_project": True,
            "statement": (
                "Wholly synthetic procurement history generated by this project from a "
                "committed seed. No real procurement record, vendor or firm is represented."
            ),
            "third_party_rights": "NONE",
        },
        # Lines by natural key — the stated total order.
        "lines": [
            _line_payload(line, band)
            for line, band in sorted(
                zip(drawn, bands, strict=True), key=lambda pair: pair[0].allocated.natural_key
            )
        ],
    }

    return envelope, drawn, offsets


def generate(root: Path | None = None) -> dict[str, Any]:
    """Produce and write the fixture, its sidecar digest, and the truth record.

    Returns the envelope. Every refusal happens before the first write.
    """
    envelope, drawn, offsets = build_envelope()

    figures = _realized_figures(envelope, drawn, offsets)

    _check_shape(drawn)
    _check_spread(drawn, offsets)
    _check_overlap(drawn)
    _check_bands([line["criticality"] for line in envelope["lines"]])
    # DV-012 and DV-013. Both were computed for the datasheet and bounded by
    # nothing until QC found them.
    check_aggregate_duration(figures["median_days"], figures["p80_days"])
    check_late_share(figures["late_count"], figures["delivered"])

    digest = dataset_content_hash(envelope)
    _write(root, envelope, digest, drawn, offsets)

    from model.procurement.datasheet import write_datasheet

    write_datasheet(envelope, figures, root)
    return envelope


def _realized_figures(
    envelope: Mapping[str, Any], drawn: Sequence[_DrawnLine], offsets: Mapping[str, float]
) -> dict[str, Any]:
    """Every figure the datasheet reports against its bounding criterion.

    Computed from the emitted envelope and the drawn lines rather than
    re-derived, so the datasheet cannot disagree with the artifact it describes.
    """
    import numpy as _np

    from model.procurement.equipment import (
        LineEquipment,
        catalog_overlap_share,
        corpus_overlap_share,
    )

    lines = envelope["lines"]
    finals = [line["events"][-1]["to_state"] for line in lines]
    delivered = sum(1 for state in finals if state == "delivered")

    totals = _np.array([sum(line.leg_days) for line in drawn], dtype=float)
    equipment = [
        LineEquipment(
            line.material_category,
            line.description,
            line.manufacturer,
            line.part_number,
            line.quantity,
            line.unit_of_measure,
        )
        for line in drawn
    ]
    ratios = [
        line.slack_days / category_expected_duration_days(line.material_category) for line in drawn
    ]
    cuts = tercile_cut_points(ratios)

    late = sum(
        1
        for emitted in lines
        if emitted["events"][-1]["to_state"] == "delivered"
        and emitted["events"][-1]["occurred_at"][:10] > emitted["need_by_date"]
    )
    # SC-024: a censored line already past its need-by at the as-of date is
    # excluded from both sides of the late share, and counted separately.
    overdue_censored = sum(
        1
        for emitted in lines
        if emitted["events"][-1]["to_state"] != "delivered"
        and AS_OF_DATE.isoformat() > emitted["need_by_date"]
    )

    delivered_totals = _np.array(
        [
            sum(line.leg_days)
            for line, emitted in zip(drawn, lines, strict=True)
            if emitted["events"][-1]["to_state"] == "delivered"
        ],
        dtype=float,
    )

    vendor_counts: dict[str, int] = {}
    project_counts: dict[str, int] = {}
    for line in drawn:
        vendor_counts[line.allocated.vendor_id] = vendor_counts.get(line.allocated.vendor_id, 0) + 1
        project_counts[line.allocated.project_id] = (
            project_counts.get(line.allocated.project_id, 0) + 1
        )
    vendor_counts = dict(sorted(vendor_counts.items()))
    project_counts = dict(sorted(project_counts.items()))

    declared_loops = rework_loop_allocation(len(drawn))
    observable = {1: 0, 2: 0, 3: 0}
    for emitted in lines:
        loops = max(0, (len(emitted["events"]) - 6) // 3)
        if loops in observable:
            observable[loops] += 1

    logs = [math.log(total) for total in totals]
    decomposition = decompose_spread(
        logs,
        [line.allocated.vendor_id for line in drawn],
        [line.material_category for line in drawn],
    )

    return {
        "delivered": delivered,
        "delivered_share": delivered / len(lines),
        "corpus_overlap_share": corpus_overlap_share(equipment, sorted(TIER_OFFSETS), {}),
        "catalog_overlap_share": catalog_overlap_share(equipment),
        "spread_ratio": decomposition.adjusted_ratio,
        "spread_ratio_unadjusted": decomposition.unadjusted_ratio,
        "median_days": float(_np.median(totals)),
        "p80_days": float(_np.percentile(totals, 80)),
        "late_share": late / delivered if delivered else 0.0,
        "late_count": late,
        "overdue_censored": overdue_censored,
        "censored_share": 1 - delivered / len(lines),
        "delivered_median_days": float(_np.median(delivered_totals)),
        "delivered_p80_days": float(_np.percentile(delivered_totals, 80)),
        "vendor_counts": vendor_counts,
        "project_counts": project_counts,
        "vendor_count_sd": float(_np.std(list(vendor_counts.values()), ddof=0)),
        "shrinkage_low": shrinkage(min(vendor_counts.values())),
        "shrinkage_high": shrinkage(max(vendor_counts.values())),
        "rework_histogram": [sum(1 for x in declared_loops if x == n) for n in (1, 2, 3)],
        "observable_rework_histogram": [observable[n] for n in (1, 2, 3)],
        "rework_lines": sum(1 for line in drawn if line.rework_loops),
        "tercile_cuts": cuts,
    }


def _check_shape(drawn: Sequence[_DrawnLine]) -> None:
    """DV-010, measured on the **emitted events** rather than recomputed.

    An earlier version asked `is_delivered_by()` — a parallel computation over
    the leg list — and got 0.874 while the emitted fixture said 0.618. Both were
    self-consistent; they were answers to different questions, because the leg
    list was one leg per loop short of the state sequence. The gate now reads the
    same thing a reader of the artifact reads: a line is delivered when its last
    emitted event says so.
    """
    delivered = 0
    occupancy = dict.fromkeys(NON_TERMINAL_STATES, 0)
    for line in drawn:
        if not line.events:
            raise GenerationError(
                f"line {line.allocated.natural_key} emitted no event; DV-007 requires at "
                f"least one, and an empty chain has no state to occupy"
            )
        final = line.events[-1][1]
        if final == "delivered":
            delivered += 1
        else:
            occupancy[final] = occupancy.get(final, 0) + 1
    check_shape_floors(len(drawn), delivered, occupancy)


def _check_spread(drawn: Sequence[_DrawnLine], offsets: Mapping[str, float]) -> None:
    logs = [math.log(sum(line.leg_days)) for line in drawn]
    check_spread_band(
        decompose_spread(
            logs,
            [line.allocated.vendor_id for line in drawn],
            [line.material_category for line in drawn],
        )
    )


def _check_overlap(drawn: Sequence[_DrawnLine]) -> None:
    from model.procurement.equipment import LineEquipment

    equipment = [
        LineEquipment(
            line.material_category,
            line.description,
            line.manufacturer,
            line.part_number,
            line.quantity,
            line.unit_of_measure,
        )
        for line in drawn
    ]
    corpus = corpus_overlap_share(equipment, sorted(TIER_OFFSETS), {})
    catalog = catalog_overlap_share(equipment)
    for label, share in (("corpus", corpus), ("catalog", catalog)):
        if share < OVERLAP_SHARE_FLOOR:
            raise GenerationError(
                f"realized {label} overlap share is {share:.4f}, below the "
                f"{OVERLAP_SHARE_FLOOR} floor"
            )


def _check_bands(bands: Sequence[int]) -> None:
    if set(bands) != {1, 2, 3, 4, 5}:
        raise GenerationError(
            f"criticality bands {sorted(set(bands))} do not cover 1-5; a band that never "
            f"occurs is a path the tier x tercile table claims to reach and does not"
        )


def _write(
    root: Path | None,
    envelope: Mapping[str, Any],
    digest: str,
    drawn: Sequence[_DrawnLine],
    offsets: Mapping[str, float],
) -> None:
    fixture = paths.fixture_path(root)
    fixture.parent.mkdir(parents=True, exist_ok=True)
    paths.truth_path(root).parent.mkdir(parents=True, exist_ok=True)

    write_payload(fixture, dict(envelope))
    write_payload(
        paths.hash_path(root),
        {
            "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "dataset_content_hash": digest,
            "hashed_object": "canonical_bytes(dataset fixture payload)",
        },
    )

    from model.procurement.equipment import LineEquipment

    equipment = [
        LineEquipment(
            line.material_category,
            line.description,
            line.manufacturer,
            line.part_number,
            line.quantity,
            line.unit_of_measure,
        )
        for line in drawn
    ]
    logs = [math.log(sum(line.leg_days)) for line in drawn]
    write_truth_record(
        paths.truth_path(root),
        build_truth_record(
            generator_id=GENERATOR_ID,
            generator_revision=GENERATOR_REVISION,
            root_seed=ROOT_SEED,
            generation_date=GENERATION_DATE,
            dataset_content_hash=digest,
            decomposition=decompose_spread(
                logs,
                [line.allocated.vendor_id for line in drawn],
                [line.material_category for line in drawn],
            ),
            vendor_offsets=offsets,
            realized_corpus_overlap_share=corpus_overlap_share(equipment, sorted(TIER_OFFSETS), {}),
            realized_catalog_overlap_share=catalog_overlap_share(equipment),
        ),
    )


def main() -> int:
    """No clock is read anywhere in this path, including here.

    A timestamp in the diagnostic output would be harmless on its own, but the
    discipline is worth keeping absolute: every date in this pipeline is a
    committed constant, and a single `now()` is how that stops being true.
    """
    envelope = generate()
    digest = dataset_content_hash(envelope)
    print(f"wrote {len(envelope['lines'])} lines at seed {ROOT_SEED}")
    print(f"  dataset_content_hash={digest}")
    return 0
