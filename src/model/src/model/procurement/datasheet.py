"""The dataset datasheet: seven named sections, nine active limitation records.

Emitted deterministically with **no clock read** — every date in it is a
committed constant, so the datasheet reproduces alongside the fixture instead of
churning on every run.

Each limitation record carries all four parts (scope decision, supporting
evidence, reversal trigger, production-scale alternative). A record missing one
is not a shorter record; it is a limitation whose reversal condition nobody
stated, which is the thing FR-016 exists to prevent. **L-5 is withdrawn** — E002
published the corpus fields that record named as its own reversal trigger — and
the remaining nine keep their original identities rather than being renumbered,
so `L-6`…`L-10` still mean what other artifacts say they mean.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from model.procurement import paths
from model.procurement.allocate import (
    REWORK_LINE_SHARE,
    VENDOR_LINE_COUNTS,
    shrinkage,
)
from model.procurement.censor import (
    AS_OF_DATE,
    CENSORED_SHARE_FLOOR,
    DELIVERED_EVENT_FLOOR,
    DELIVERED_SHARE_FLOOR,
    ORDER_DATE_WINDOW,
)
from model.procurement.criticality import BAND_TABLE, PRESSURE_LEVELS, SLACK_MEAN, SLACK_SD, TIERS
from model.procurement.durations import (
    FORWARD_SHARES,
    LOOP_SHARES,
    MEDIAN_TARGET_DAYS,
    P80_TARGET_DAYS,
    SIGMA_0,
    SIGMA_C,
    SIGMA_R,
    SIGMA_W,
    T_PRE,
    TAU,
    TIER_OFFSETS,
    category_expected_duration_days,
)
from model.procurement.equipment import OVERLAP_SHARE_FLOOR

__all__ = [
    "ACTIVE_LIMITATIONS",
    "LIMITATION_PARTS",
    "SECTION_TITLES",
    "WITHDRAWN_LIMITATIONS",
    "DatasheetError",
    "LimitationRecord",
    "render",
    "write_datasheet",
]

#: FR-014's seven sections, in order.
SECTION_TITLES = (
    "Motivation",
    "Composition",
    "Collection Process",
    "Preprocessing",
    "Uses",
    "Distribution",
    "Maintenance",
)

#: All four are mandatory on every record (DV-019).
LIMITATION_PARTS = (
    "scope_decision",
    "supporting_evidence",
    "reversal_trigger",
    "production_scale_alternative",
)

#: Withdrawn, not renumbered. The identities other artifacts cite must keep
#: meaning what they meant.
WITHDRAWN_LIMITATIONS = ("L-5",)


class DatasheetError(ValueError):
    """Raised when the datasheet would be emitted incomplete or non-conforming."""


@dataclass(frozen=True, slots=True)
class LimitationRecord:
    identifier: str
    subject: str
    scope_decision: str
    supporting_evidence: str
    reversal_trigger: str
    production_scale_alternative: str

    def parts(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in LIMITATION_PARTS}


ACTIVE_LIMITATIONS: tuple[LimitationRecord, ...] = (
    LimitationRecord(
        "L-1",
        "Insufficient for validating vendor-level tail behaviour",
        "199 lines across 12 vendors gives the smallest vendor 5 observations, which cannot "
        "support a claim about that vendor's tail.",
        f"The declared per-vendor vector runs {max(VENDOR_LINE_COUNTS.values())} down to "
        f"{min(VENDOR_LINE_COUNTS.values())}; shrinkage at the small end is "
        f"{shrinkage(min(VENDOR_LINE_COUNTS.values())):.2f}, so the pooled estimate dominates.",
        "A dataset with at least ~50 lines for the smallest vendor would make per-vendor tail "
        "estimates identifiable without heavy pooling.",
        "Production scale: draw the vendor mix from realized purchase history rather than "
        "declaring it, and accept whatever tail support that gives.",
    ),
    LimitationRecord(
        "L-2",
        "Reproducibility claim is scoped to a pinned environment",
        "FR-021's digest is claimed only under the recorded `library_pin`; another NumPy "
        "version may produce a different stream and therefore a different digest.",
        "`procurement-validate` reports a scope limit rather than a pass when the observed "
        "version differs from the recorded pin.",
        "A pure-Python draw, or a stream specified independently of any library version, "
        "would remove the dependency entirely.",
        "Production scale: pin the environment in a lockfile and regenerate on upgrade, "
        "treating the digest change as an expected consequence rather than a defect.",
    ),
    LimitationRecord(
        "L-3",
        "Rework rate is declared, not cited",
        f"{REWORK_LINE_SHARE:.0%} of lines carry rework because the design declares it; no "
        f"published source states that figure for this equipment class.",
        "`allocate.rework_loop_allocation` fixes the allocation before generation, and DV-009 "
        "asserts the realized histogram equals it exactly.",
        "A published rework rate for engineered equipment procurement, or an internal one "
        "measured from real submittal logs, would replace the declaration.",
        "Production scale: measure the rate from the organisation's own submittal history.",
    ),
    LimitationRecord(
        "L-4",
        "FR-008's spread-ratio target and band are derived, not cited",
        "The 0.24 target and the 0.12–0.49 band come from a shrinkage identity applied to "
        "FR-007's published median and P80, not from a source that states them.",
        f"σ_w = {SIGMA_W} back-solved from {P80_TARGET_DAYS:.0f}/{MEDIAN_TARGET_DAYS:.0f}; "
        f"τ = {TAU} as 0.24 × σ_w. **The derivation carries no category term**, while the "
        f"ratio actually asserted against the band is the category-adjusted τ/σ_r ≈ "
        f"{TAU / SIGMA_R:.4f} — so the published target and the measured quantity are not the "
        f"same number (analysis finding A-020, carried open).",
        "A published between-vendor variance component for procurement lead times would "
        "replace the derivation and close the gap between the two ratios.",
        "Production scale: fit the variance decomposition on real delivery history and use "
        "the fitted ratio rather than a target.",
    ),
    LimitationRecord(
        "L-6",
        "The duration model is a stand-in for real lead-time behaviour",
        "Per-transition lognormal draws with declared apportionment shares are a modelling "
        "convenience, not an observed process.",
        f"Family, σ₀ = {SIGMA_0}, the apportionment {FORWARD_SHARES}, the whole-day rounding "
        f"and the 1-day floor are all disclosed in § Collection Process.",
        "Transition-level timestamps from a real procurement system would replace the model "
        "with measurement.",
        "Production scale: fit per-transition distributions from the organisation's own "
        "lifecycle event log.",
    ),
    LimitationRecord(
        "L-7",
        "No row-level generation provenance",
        "A loaded row carries `roster_hash` but not the dataset content hash or the generator "
        "revision, so it is traceable to the roster it was generated against, not to the run "
        "that produced it.",
        "`purchase_order_line` has exactly one provenance column; the dataset content hash "
        "lives in the sidecar and the ground-truth record, neither of which is loaded.",
        "A `dataset_content_hash` column on every row would make each row traceable to its "
        "run, at the cost of one repeated constant per row.",
        "Production scale: carry a load-batch identifier and join provenance through it, "
        "rather than repeating the digest per row.",
    ),
    LimitationRecord(
        "L-8",
        "The post-split uncensored event count is bounded only through an assumed fraction",
        "FR-033 assumes a held-out fraction of 0.25 to reason about the post-split event "
        "count; this epic neither performs the split nor observes the fraction used.",
        "FR-028 emits no split and no split label; ownership of the split is recorded as "
        "unassigned in the registered documents.",
        "The epic that owns the split publishing its fraction would replace the assumption "
        "with a read value.",
        "Production scale: perform the split where the evaluation protocol is defined and "
        "record the realized fraction alongside it.",
    ),
    LimitationRecord(
        "L-9",
        "Corpus overlap is at vocabulary level, not instance level",
        "Overlap is on shared vocabulary — category token, description composition, quantity "
        "domain, vendor name, and now manufacturer and part number — not on a document "
        "reference; the material-item tag will rarely coincide with a real corpus item.",
        f"The realized corpus-overlap share is measured against a ≥{OVERLAP_SHARE_FLOOR:.0%} "
        f"floor and recorded; the join that holds exactly is category + vendor + the "
        f"material-item stem with the parenthetical tag normalised out.",
        "An instance-level key published by E002 — a stable item identifier appearing in both "
        "the corpus and this dataset — would make the join exact.",
        "Production scale: join on the organisation's own material master, where the item "
        "identifier is shared by construction.",
    ),
    LimitationRecord(
        "L-10",
        "The late-delivery band sits below the figure in its own source sentence",
        "FR-011 targets 25–35% of delivered lines missing their need-by date, while the "
        "published sentence FR-007's 61/94 pair comes from states 38%.",
        "The departure is recorded rather than reconciled; the realized late share is "
        "measured and published in § Composition.",
        "Reconciling the two would mean either adopting 38% as the target or explaining why "
        "this dataset's population differs from the published one.",
        "Production scale: measure the late share from realized deliveries rather than "
        "targeting it.",
    ),
)


def _generation_process(envelope: Mapping[str, Any]) -> list[str]:
    lines = [
        f"- **Generator identity and revision**: `{envelope['generator_id']}` "
        f"revision {envelope['generator_revision']}",
        f"- **Root seed**: `{envelope['root_seed']}`",
        f"- **Seed derivation scheme**: `{envelope['seed_derivation']}`",
        f"- **Generation date**: {envelope['generation_date']} (a committed constant, "
        f"never the run date)",
        f"- **Layer label**: `{envelope['layer']}`",
        "- **Content hash of every generation input** — all "
        f"{len(envelope['generation_inputs'])}, each with the convention it was hashed under:",
    ]
    for entry in envelope["generation_inputs"]:
        lines.append(f"  - `{entry['path']}` — `{entry['digest']}` (`{entry['digest_kind']}`)")
    return lines


def _duration_disclosure() -> list[str]:
    return [
        "- **Family**: lognormal per transition, drawn on the log scale.",
        f"- **Parameterization** (the generator's own, not a re-expressed one): per-leg "
        f"location `ln(share × T_pre) − σ₀²/2 + b_v + c_k`, scale `σ₀ = {SIGMA_0}`.",
        f"- **Pre-rework aggregate mean** `T_pre = {T_PRE}`, calibrated by simulation so the "
        f"rework-inclusive median and P80 land on {MEDIAN_TARGET_DAYS:.0f} and "
        f"{P80_TARGET_DAYS:.0f}.",
        "- **Time unit**: whole days.",
        "- **Rounding rule**: `round()` to the nearest whole day.",
        "- **Minimum-duration floor**: 1 day. Load-bearing rather than cosmetic — a zero-day "
        "leg would make `occurred_at` non-increasing. It biases short legs upward.",
        f"- **Forward apportionment**: {FORWARD_SHARES}, summing to 1.0.",
        f"- **Rework loop**: {LOOP_SHARES} — three transitions per loop, not two; the third "
        f"returns to `under_review` at the forward share it always carries.",
        f"- **Spread components**: σ_w = {SIGMA_W}, τ = {TAU}, σ_c = {SIGMA_C}, "
        f"σ_r = {SIGMA_R:.4f}.",
    ]


def _category_disclosure() -> list[str]:
    lines = [
        "Two **distinctly named** duration quantities, which must never be written where the "
        "other is meant (FR-035):",
        "",
        "- `category_expected_duration_days` = `exp(μ_base + c_k + σ_w²/2)` — a property of "
        "the **category**.",
        "- `line_expected_total_duration_days` = the same with the vendor offset `b_v` added "
        "— a property of the **line**. FR-011's need-by derivation uses this one.",
        "",
        "| Tier | Log offset | Expected duration (days) | Categories |",
        "|---|---|---|---|",
    ]
    for tier_offset, tier in ((0.20, "T1"), (0.00, "T2"), (-0.40, "T3")):
        members = sorted(c for c, off in TIER_OFFSETS.items() if off == tier_offset)
        expected = category_expected_duration_days(members[0])
        lines.append(
            f"| **{tier}** | {tier_offset:+.2f} | {expected:.1f} | "
            f"{', '.join(f'`{m}`' for m in members)} |"
        )
    lines += [
        "",
        "Tier offsets are mean-zero at the declared line weights, so a category term cannot "
        "shift the aggregate target.",
        "",
        "| Tier \\ Pressure | " + " | ".join(f"`{p}`" for p in PRESSURE_LEVELS) + " |",
        "|---|" + "---|" * len(PRESSURE_LEVELS),
    ]
    for tier in TIERS:
        cells = " | ".join(str(BAND_TABLE[(tier, level)]) for level in PRESSURE_LEVELS)
        lines.append(f"| **{tier}** | {cells} |")
    lines += [
        "",
        f"Slack is **multiplicative** on the line's expected duration: "
        f"`f ~ Normal({SLACK_MEAN}, {SLACK_SD})` truncated at 0. Criticality is **derived** "
        f"and slack is **drawn**, in that direction — there is no cycle.",
    ]
    return lines


def _realized(envelope: Mapping[str, Any], realized: Mapping[str, Any]) -> list[str]:
    rows = [
        ("Line count", "190–210 (FR-003)", str(len(envelope["lines"]))),
        (
            "Delivered share",
            f"[max(80%, {DELIVERED_EVENT_FLOOR}/N), {1 - CENSORED_SHARE_FLOOR:.0%}] (FR-010)",
            f"{realized['delivered_share']:.3f}",
        ),
        (
            "Uncensored delivery events",
            f"≥ max({DELIVERED_SHARE_FLOOR:.0%} × N, {DELIVERED_EVENT_FLOOR}) (FR-010)",
            str(realized["delivered"]),
        ),
        (
            "Corpus-overlap share",
            f"≥ {OVERLAP_SHARE_FLOOR:.0%} (FR-032, SC-025)",
            f"{realized['corpus_overlap_share']:.3f}",
        ),
        (
            "Catalog-overlap share",
            f"≥ {OVERLAP_SHARE_FLOOR:.0%} (FR-034, SC-026)",
            f"{realized['catalog_overlap_share']:.3f}",
        ),
        (
            "Spread ratio, category-adjusted",
            "0.12–0.49 inclusive (FR-008, FR-036, SC-007)",
            f"{realized['spread_ratio']:.4f}",
        ),
        (
            "Spread ratio, unadjusted",
            "recorded, not bounded (FR-036)",
            f"{realized['spread_ratio_unadjusted']:.4f}",
        ),
        (
            "Aggregate median duration",
            f"{MEDIAN_TARGET_DAYS:.0f} ± 5 days (SC-023)",
            f"{realized['median_days']:.1f}",
        ),
        (
            "Aggregate P80 duration",
            f"{P80_TARGET_DAYS:.0f} ± 8 days (SC-023)",
            f"{realized['p80_days']:.1f}",
        ),
        (
            "Late-delivery share",
            "25–35% of delivered lines (FR-011)",
            f"{realized['late_share']:.3f}",
        ),
        (
            "Rework lines",
            f"{REWORK_LINE_SHARE:.0%} of N, declared (FR-006, DV-009)",
            str(realized["rework_lines"]),
        ),
    ]
    out = ["| Figure | Intended, and its bounding criterion | Realized |", "|---|---|---|"]
    out += [f"| {name} | {intended} | **{value}** |" for name, intended, value in rows]
    return out


def render(envelope: Mapping[str, Any], realized: Mapping[str, Any]) -> str:
    """The whole datasheet. Deterministic — no clock, no environment read."""
    parts: list[str] = ["# Dataset Datasheet — Synthetic Procurement History", ""]

    parts += [f"## 1. {SECTION_TITLES[0]}", ""]
    parts += [
        "This dataset exists so the procurement-risk demonstration has a delivery history to "
        "reason over without using any real procurement record. It is **wholly synthetic**: "
        "no real firm, vendor, project or purchase order is represented.",
        "",
        "It was created to support forecasting and identity-resolution work downstream, and "
        "to make those claims falsifiable — the parameters it was generated from are "
        "published in a separate ground-truth record, so a later claim to have recovered them "
        "can be checked rather than believed.",
        "",
    ]

    parts += [f"## 2. {SECTION_TITLES[1]}", ""]
    parts += _realized(envelope, realized)
    parts += [
        "",
        f"Each line carries six descriptive fields, all present and non-blank. "
        f"{realized['catalog_overlap_share']:.1%} of lines draw manufacturer and part number "
        f"from E002's published catalog with the manufacturer's category list containing the "
        f"line's own category; the complement draws a **category-mismatched** catalog entry, "
        f"so the overlap share is a measurement that can fall below its floor rather than an "
        f"artefact of construction.",
        "",
    ]

    parts += [f"## 3. {SECTION_TITLES[2]}", ""]
    parts += _generation_process(envelope)
    parts += ["", "### Per-transition duration assumptions", ""]
    parts += _duration_disclosure()
    parts += [
        "",
        f"**Calendar**: order dates fall in [{ORDER_DATE_WINDOW.first}, "
        f"{ORDER_DATE_WINDOW.last}]; the as-of date is {AS_OF_DATE}. All three are committed "
        f"constants — none is read from a clock, because a run-date default would move the "
        f"content hash the day after generation while the recorded seed still looked honoured.",
        "",
    ]

    parts += [f"## 4. {SECTION_TITLES[3]}", ""]
    parts += _category_disclosure()
    parts += [
        "",
        f"**Tercile cut points** over the realized dataset: "
        f"{', '.join(f'{c:.4f}' for c in realized['tercile_cuts'])}. Computed over the dataset "
        f"as a whole rather than within each category, so the tier dimension of the table "
        f"stays informative.",
        "",
    ]

    parts += [f"## 5. {SECTION_TITLES[4]}", ""]
    parts += [
        "**Supports**: lead-time forecasting, right-censoring handling, partial pooling across "
        "vendors, and cross-document identity resolution against E002's synthetic corpus.",
        "",
        "**Does not evidence**: any claim about real vendors, real lead times, or real "
        "procurement risk. Nothing here is measurement.",
        "",
        "**No train/evaluation split is emitted**, and no split label appears anywhere in the "
        "artifact set. **Ownership of the split is unassigned** in the registered documents: "
        "this epic neither performs it nor names who does. The 0.25 held-out fraction that "
        "appears in FR-033's reasoning is an **assumed cross-epic fraction**, used only to "
        "bound the post-split event count, and it is not a value this dataset observes or "
        "commits anyone to.",
        "",
    ]

    parts += [f"## 6. {SECTION_TITLES[5]}", ""]
    parts += [
        "The fixture is **not a corpus document** and carries **no corpus manifest entry** — "
        "it is a dataset, and listing it in the corpus manifest would make it discoverable as "
        "a document to extract from.",
        "",
        f"**Licence basis**: `{envelope['license_basis']['basis_id']}`. "
        f"{envelope['license_basis']['statement']} "
        f"Third-party rights: `{envelope['license_basis']['third_party_rights']}`.",
        "",
    ]

    parts += [f"## 7. {SECTION_TITLES[6]}", ""]
    parts += [
        "**Regeneration**: `procurement-generate` rewrites the fixture, its sidecar digest and "
        "the ground-truth record from the committed seed. `procurement-validate` regenerates "
        "and compares the digest.",
        "",
        "**A roster or category-map edit invalidates the recorded digest** and requires "
        "regeneration rather than patching. Editing the fixture by hand leaves it disagreeing "
        "with its own sidecar, which `procurement-validate` refuses.",
        "",
    ]

    parts += ["## Limitations", ""]
    parts += [
        f"**{len(ACTIVE_LIMITATIONS)} active records.** `{'`, `'.join(WITHDRAWN_LIMITATIONS)}` "
        f"is **withdrawn** — E002 published the corpus fields that record named as its own "
        f"reversal trigger. The remaining records are **not renumbered**, so the identities "
        f"other artifacts cite still mean what they meant.",
        "",
    ]
    for record in ACTIVE_LIMITATIONS:
        parts += [
            f"### {record.identifier} — {record.subject}",
            "",
            f"- **Scope decision**: {record.scope_decision}",
            f"- **Supporting evidence**: {record.supporting_evidence}",
            f"- **Reversal trigger**: {record.reversal_trigger}",
            f"- **Production-scale alternative**: {record.production_scale_alternative}",
            "",
        ]

    return "\n".join(parts).rstrip("\n") + "\n"


def write_datasheet(
    envelope: Mapping[str, Any], realized: Mapping[str, Any], root: Path | None = None
) -> Path:
    text = render(envelope, realized)
    check_limitations(ACTIVE_LIMITATIONS)
    target = paths.datasheet_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    return target


def check_limitations(records: Sequence[LimitationRecord]) -> int:
    """DV-019's second half: 100% of records carry all four parts."""
    if not records:
        raise DatasheetError("a datasheet with no limitation record discloses nothing")
    for record in records:
        missing = [name for name, value in record.parts().items() if not str(value).strip()]
        if missing:
            raise DatasheetError(
                f"limitation {record.identifier} is missing {', '.join(missing)}. A record "
                f"short of a part is not a shorter record — it is a limitation whose "
                f"reversal condition nobody stated"
            )
    return len(records)
