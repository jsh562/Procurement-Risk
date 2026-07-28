"""The two reports this job emits: the run report, and the refusal report.

Markdown and not JSON, because FR-020, FR-027 and FR-045 each name a
*reader-facing* artifact and E005's datasheet is the precedent. The schema is
closed — `SECTION_TITLES` and `SECTION_FIELDS` are the whole of it — so SC-026's
absence check stays a structural predicate over declared fields rather than the
term search FR-040 rejects. Every measure carrying a decision criterion is
rendered as FR-038's four-part unit; the wall clock and the realized shape are
rendered with no verdict, because neither has a criterion to be judged against.

The **refusal report** (FR-037) is the second kind and has a closed schema of
its own. One file per *attempt*, named by an identifier built from the as-of
date, the input row hash and the attempt's instant, because a refused attempt
has no `run_id` — FR-017 forbids writing the run row — and two refusals of one
input would otherwise have nothing distinguishing their evidence. It is never
overwritten, and it carries the same field set as the stderr reason, since the
stream is transient while the file is the durable half of the pair G-8 names.
Writing it is not a write to any store SC-015 or DV-013 ranges over.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from model.forecast.ablation import (
    FLOOR_INTERVAL_PROBABILITY,
    KaplanMeierFloor,
    RealizedDelta,
)
from model.forecast.censoring import terminal_event
from model.forecast.config import CHAINS_MIN
from model.forecast.diagnostics import DiagnosticRow, direction_prose
from model.forecast.manifest import VENDOR_SHRINKAGE_HDI_PROBABILITY, RunManifest
from model.forecast.paths import (
    REFUSAL_REPORT_PREFIX,
    RUN_REPORT_PREFIX,
    refusal_report_path,
    refused_attempt_id,
    run_report_path,
)
from model.forecast.read import ProcurementInput
from model.forecast.shrinkage import VendorShrinkage

__all__ = [
    "ABLATION_COMPARATOR_LABEL",
    "EMITTED_REPORT_KINDS",
    "LIMITATION_IDENTIFIERS",
    "LIMITATION_PARTS",
    "NOTHING_SAMPLED",
    "REFUSAL_DIAGNOSTIC_FIELDS",
    "REFUSAL_PRECONDITION_FIELDS",
    "REFUSAL_SECTION_TITLES",
    "REGISTERED_COVERAGE_BAND",
    "REGISTERED_UNCENSORED_EVENT_ASSUMPTION",
    "SECTION_FIELDS",
    "SECTION_TITLES",
    "SHRINKAGE_SUPPORT_THRESHOLD",
    "AblationOutcome",
    "LimitationRecord",
    "RefusedAttempt",
    "ReportError",
    "SampledShape",
    "UnmetPrecondition",
    "check_limitations",
    "limitations",
    "maximum_observed_duration_days",
    "render_refusal_report",
    "render_run_report",
    "vendor_claim_observation_floor",
    "write_refusal_report",
    "write_run_report",
]


class ReportError(ValueError):
    """Raised when the report would be emitted incomplete or non-conforming.

    A `ValueError`, following `DatasheetError`: every case is something the
    caller handed over — a limitation short of a part, a vendor with a weight and
    no observation count, a manifest whose measurements do not agree with the
    rows the report is describing.
    """


# ---------------------------------------------------------------------------
# The closed schema (FR-040)
# ---------------------------------------------------------------------------

#: The sections, in order. **The censoring ablation was deliberately absent
#: until there was something to put in it**: it is FR-033's measure, computed by
#: `ablation.py`, and a section rendered ahead of that would have been a declared
#: field carrying a placeholder — which is the one way a closed schema stops
#: being a check. T050 and T051 landed the floor and the delta, so the section is
#: here now and the renderer requires both: the entry cannot be emitted empty and
#: cannot be skipped.
SECTION_TITLES: tuple[str, ...] = (
    "Run Identity",
    "Input Provenance",
    "Sampling Shape",
    "Split and Held-Out Evidence",
    "Censoring Ablation",
    "Per-Vendor Shrinkage",
    "Horizon and Extrapolation",
    "Limitations",
    "Emitted Report Set",
)

#: Every field name any section may carry. **This is the closed schema.** DV-041
#: validates that each rendered field belongs to it, and DV-021 checks the
#: absence of a coverage threshold or a calibration verdict *over this list*
#: rather than by searching the prose — which is what removes the carve-out
#: SC-025 would otherwise force, since the registered coverage band is a field a
#: reader is owed and a term search would have to make an exception for.
SECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "Run Identity": (
        "Run identifier",
        "As-of date",
        "Code revision",
        "Worktree state",
        "Model version",
        "Artifact schema version",
        "Active pointer",
    ),
    "Input Provenance": (
        "Input layer",
        "Datasheet reference",
        "Input row hash",
        "Serialization convention",
        "Fixture file digest",
        "Fixture digest agreement",
        "Roster hash",
    ),
    "Sampling Shape": (
        "Chains",
        "Draws per line",
        "Tuning draws per chain",
        "Grid horizon",
        "Draw serialization",
        "Artifact hash",
        "Wall clock",
        "Sampling seed entropy",
    ),
    "Split and Held-Out Evidence": (
        "Declared held-out fraction",
        "Realized held-out fraction",
        "Split seed entropy",
        "Split assignment hash",
        "Training lines",
        "Open lines forecast",
        "Realized held-out uncensored event count",
    ),
    "Censoring Ablation": (
        "Comparator",
        "Realized delta",
        "Interval",
        "Seeds",
        "Per-seed deltas",
        "Decision criterion",
        "Criterion derivation",
        "Verdict",
    ),
    "Per-Vendor Shrinkage": (
        "Credible level",
        "Vendor-claim observation floor",
        "Vendor",
        "Training lines",
        "Realized shrinkage weight",
        "Interval",
        "Vendor-level claim",
    ),
    "Horizon and Extrapolation": (
        "Grid horizon",
        "Maximum observed duration",
        "Extrapolation beyond the observed maximum",
    ),
    "Limitations": (
        "Identifier",
        "Subject",
        "Scope decision",
        "Supporting evidence",
        "Reversal trigger",
        "Production-scale alternative",
    ),
    "Emitted Report Set": ("Report kind", "This file"),
}

#: FR-040's three report kinds, enumerated rather than categorised. Named here
#: because the run report states its own membership: a reader holding one of the
#: three should not have to consult another document to learn what the set is.
EMITTED_REPORT_KINDS: tuple[tuple[str, str], ...] = (
    ("run report", f"`{RUN_REPORT_PREFIX}-<run_id>.md`, emitted by the fit job on a run "
     "that ships"),
    ("reproduction report", "emitted by the reproduction job"),
    ("refusal report", f"`{REFUSAL_REPORT_PREFIX}-<attempt>.md`, emitted on any refusal"),
)

#: All four parts are mandatory on every record (FR-027, DV-024). Same tuple
#: E005's datasheet carries, for the same reason: a record short of a part is not
#: a shorter record, it is a limitation whose reversal condition nobody stated.
LIMITATION_PARTS: tuple[str, ...] = (
    "scope_decision",
    "supporting_evidence",
    "reversal_trigger",
    "production_scale_alternative",
)

#: The four records `data-model.md` § Disclosed Limitations declares, **by
#: identity**. DV-037 requires each to be present rather than requiring four
#: well-formed limitations of some kind, which DV-024 already covers.
LIMITATION_IDENTIFIERS: tuple[str, ...] = ("L-1", "L-2", "L-3", "L-4")

#: The coverage band `specs/prd.md` registers, restated here and **asserted by
#: nothing in this epic**. FR-026 forbids E007 publishing a coverage threshold of
#: its own; SC-025 and limitation L-3 require the realized event count to be
#: published against the band a registered document already carries. Recording
#: whose band it is, in the field beside it, is what keeps those two compatible.
REGISTERED_COVERAGE_BAND = (0.73, 0.87)

#: The event count `specs/prd.md` derives that band from. L-3's shortfall is the
#: gap between it and what a 0.25 split of this dataset actually realizes.
REGISTERED_UNCENSORED_EVENT_ASSUMPTION = 120

#: Principle VIII's label, carried into the report rather than left to the
#: reader. The censoring-ignoring fit is this epic's own model with one term
#: removed, so it is **an ablation comparator and never a baseline** — an
#: ablation beaten by the full model is the weakest comparison available, and a
#: reader who took it for a baseline would read a much stronger claim than the
#: one this run makes (FR-033's coverage row).
ABLATION_COMPARATOR_LABEL = (
    "censoring-ignoring fit — **an ablation comparator, not a baseline**. It is this run's own "
    "model with the censoring contribution removed and nothing else changed, so beating it is "
    "evidence about that term alone and is not a claim about this model against any alternative "
    "a reader might otherwise use (Principle VIII)."
)

#: `spec.md` § Published Constants: the vendor-claim observation floor is "the
#: smallest training-line count at which realized shrinkage reaches **0.5**" —
#: the point at which a vendor's estimate stops being majority-borrowed from the
#: population. A **rule** and not a number, because the realized weights are what
#: determine the count and a number chosen after seeing them is the move FR-028
#: prohibits for bands.
SHRINKAGE_SUPPORT_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# The censoring ablation (T052 — FR-033, FR-038)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AblationOutcome:
    """The realized delta and the floor it is judged against, in one record.

    One object rather than two arguments, for the reason FR-033 states outright:
    "a delta and an interval with no bar next to them do not resolve to the
    demonstration SC-008 claims". A caller able to supply the measurement without
    the criterion would eventually publish one without the other, and the floor
    is the entire reason the comparison counts as one.

    The two arrive from different places on purpose — the floor from
    `kaplan_meier_floor`, which cannot reach a fitted quantity, and the delta
    from repeated pairs of fits — and they are commensurable because both are
    relative shortenings of the same censoring-aware figure.
    """

    delta: RealizedDelta
    floor: KaplanMeierFloor

    @property
    def met(self) -> bool:
        """Whether the realized delta reaches the independently derived floor.

        The comparison is a floor, so the direction is "at or above". Stated as a
        property rather than recomputed at each use, so the verdict rendered in
        the report and any verdict a caller draws are the same comparison.
        """
        return self.delta.delta >= self.floor.floor


def _ablation_section(outcome: AblationOutcome) -> list[str]:
    """FR-038's unit for SC-008: measure, value, criterion with direction, verdict.

    All four parts, in one place. The delta with the interval the repeated seeds
    produced; the floor **beside it** rather than elsewhere in the document, with
    the direction spelled out, because a value and a bar do not resolve to a
    verdict for a reader who does not already know which is which; and the
    verdict itself, which is the part this epic's own history records going
    missing.

    A missed floor is rendered as a published shortfall and not as a defect
    (SC-008's stated disposition, Principle VII): the delta's magnitude is an
    empirical property of this dataset's censoring level rather than of the
    estimator, and the registered coverage band's shortfall in § Limitations is
    the precedent for publishing a number the data does not reach.
    """
    delta, floor = outcome.delta, outcome.floor
    verdict = (
        f"**met** — the realized delta {delta.delta:.4f} is at or above the derived floor "
        f"{floor.floor:.4f}."
        if outcome.met
        else (
            f"**missed** — the realized delta {delta.delta:.4f} falls below the derived floor "
            f"{floor.floor:.4f}, a shortfall of {floor.floor - delta.delta:.4f}. Published as a "
            f"shortfall rather than treated as a defect: the margin is an empirical property of "
            f"this input's censoring level, not of the estimator, and the floor is not adjusted "
            f"to meet it (Principle VII)."
        )
    )
    return [
        f"- **Comparator**: {ABLATION_COMPARATOR_LABEL}",
        f"- **Realized delta**: {delta.delta:.4f} — the aggregate median forecast over open "
        f"lines is that fraction shorter under the comparator than under the full fit, "
        f"`(aware − ignoring) / aware`. Signed, so a comparator that came out *longer* reads "
        f"as a negative number rather than as a large positive one.",
        f"- **Interval**: [{delta.interval_low:.4f}, {delta.interval_high:.4f}] — the range the "
        f"repeated seeds produced, not a normal approximation around them. Never a single-seed "
        f"pass (FR-033).",
        f"- **Seeds**: {', '.join(str(seed) for seed in delta.seeds)} — {len(delta.seeds)} "
        f"repetitions, each one pair of fits differing only in the censoring term.",
        f"- **Per-seed deltas**: "
        f"{', '.join(f'{per_seed:.4f}' for per_seed in delta.per_seed_deltas)}, aligned with "
        f"the seeds above so the summary can be checked rather than taken.",
        f"- **Decision criterion**: at or above a floor of {floor.floor:.4f} — a **floor**, so "
        f"the passing direction is upward. Interval "
        f"[{floor.interval_low:.4f}, {floor.interval_high:.4f}] at "
        f"{FLOOR_INTERVAL_PROBABILITY:.2f}; the Kaplan–Meier median is itself an estimate, and "
        f"a bare number for it would be the shape Principle II refuses.",
        f"- **Criterion derivation**: the input's own censoring bias, measured on the "
        f"**training split alone** ({floor.training_line_count} lines) and **before and "
        f"independently of the fit** — a Kaplan–Meier median of "
        f"{floor.kaplan_meier_median:.1f} days against a naive completed-duration mean of "
        f"{floor.naive_completed_mean:.1f} days over the same rows, "
        f"`(median − mean) / median`. Non-parametric and never back-solved from the fitted "
        f"model, which would compare a measurement against a derivation of the same quantity "
        f"(AD-008, FR-033).",
        f"- **Verdict**: {verdict}",
    ]


# ---------------------------------------------------------------------------
# Limitation records (FR-027)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LimitationRecord:
    """One disclosed limitation, in four parts. `datasheet.py`'s shape reused."""

    identifier: str
    subject: str
    scope_decision: str
    supporting_evidence: str
    reversal_trigger: str
    production_scale_alternative: str

    def parts(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in LIMITATION_PARTS}


def check_limitations(records: Sequence[LimitationRecord]) -> int:
    """DV-024's four-part form and DV-037's presence-by-identity, in one pass.

    Two claims rather than one, because a set of four well-formed records that
    omits `L-2` satisfies the first and fails the second — which is exactly the
    gap NC-23 plants and NC-8 does not reach.
    """
    if not records:
        raise ReportError("a run report with no limitation record discloses nothing")
    for record in records:
        missing = [name for name, value in record.parts().items() if not str(value).strip()]
        if missing:
            raise ReportError(
                f"limitation {record.identifier} is missing {', '.join(missing)}. A record "
                f"short of a part is not a shorter record — it is a limitation whose "
                f"reversal condition nobody stated"
            )
    present = [record.identifier for record in records]
    absent = [name for name in LIMITATION_IDENTIFIERS if name not in present]
    if absent:
        raise ReportError(
            f"the limitation set omits {', '.join(absent)}. `data-model.md` declares four by "
            f"identity, and a set of four well-formed records that happens not to include "
            f"L-2 discloses nothing about the horizon's extrapolation (DV-037)"
        )
    return len(records)


def maximum_observed_duration_days(
    procurement_input: ProcurementInput, as_of_date: date
) -> int | None:
    """The longest duration the input actually observes, computed — not asserted.

    G-10 is why this exists: FR-031 requires disclosing how the 365-day grid
    compares to the longest observed duration, and E005's datasheet publishes a
    median (58.0) and a P80 (90.4) but **never a maximum**. The claim had no
    source, so it is measured here instead, under G-12's convention —
    `terminal.occurred_at::date − order_date`, whole days — over the lines whose
    terminal event has occurred at or before the anchor.

    `None` when no line has delivered at the anchor. Returned rather than raised,
    because "nothing has finished yet" is a legitimate state for an early as-of
    date and the limitation is then restated to what the data supports.
    """
    durations = []
    for line in procurement_input.lines:
        terminal = terminal_event(line)
        if terminal is None:
            continue
        occurred = terminal.occurred_at.astimezone(UTC).date()
        if occurred <= as_of_date:
            durations.append((occurred - line.order_date).days)
    return max(durations) if durations else None


def limitations(
    manifest: RunManifest,
    *,
    maximum_observed_days: int | None,
    smallest_vendor_training_lines: int,
    observation_floor: int | None,
) -> tuple[LimitationRecord, ...]:
    """`L-1`–`L-4`, each in four parts, with every figure a measurement.

    Three of the four carry a number this run produced rather than a number
    quoted from another document: `L-2` the computed maximum observed duration,
    `L-3` the realized held-out uncensored event count, `L-4` the realized
    observation floor and the smallest vendor's realized training count. That is
    the difference between disclosing a limitation and restating one.
    """
    horizon = manifest.horizon_days
    if maximum_observed_days is None:
        horizon_evidence = (
            f"No line has delivered at {manifest.as_of_date}, so the input observes no "
            f"completed duration at all and the whole {horizon}-day grid is the fitted "
            f"family's shape. The claim is restated to what the data supports rather than "
            f"kept: nothing here evidences that the horizon is long enough or short enough."
        )
        extrapolated = horizon
    else:
        extrapolated = horizon - maximum_observed_days
        horizon_evidence = (
            f"The longest duration the input observes is **{maximum_observed_days} days**, "
            f"computed from `lifecycle_event` under the convention "
            f"`terminal.occurred_at::date − order_date` because the datasheet publishes a "
            f"median and a P80 and never a maximum (G-10). The grid runs {horizon} days, so "
            f"**{extrapolated} days** of every curve sit past anything the data observes."
        )
    events = manifest.held_out_uncensored_event_count
    low, high = REGISTERED_COVERAGE_BAND
    floor_evidence = (
        f"the smallest vendor carries {smallest_vendor_training_lines} training line(s)"
        if observation_floor is None
        else (
            f"realized shrinkage reaches {SHRINKAGE_SUPPORT_THRESHOLD:.2f} at "
            f"{observation_floor} training lines, and the smallest vendor carries "
            f"{smallest_vendor_training_lines}"
        )
    )
    return (
        LimitationRecord(
            "L-1",
            "The fit's structure matches the structure its input was generated from",
            "E007 fits a hierarchical model over vendor and material category with partial "
            "pooling; E005 generated the durations from additive log-scale vendor offsets and "
            "three material-category tier offsets. Recovery of those effects is a check that "
            "the estimator works on data from its own family, not evidence that the family "
            "fits procurement. Disclosed rather than avoided.",
            "E005's datasheet publishes σ_w = 0.51, τ = 0.1224 and σ_c = 0.219 with the tier "
            "offsets, and its ground-truth record holds the twelve per-vendor offsets. The "
            "generative form is a lognormal per transition while this run models total "
            "duration, so the match is structural rather than exact — the aggregate is a sum "
            "of lognormals rather than a lognormal.",
            "A dataset this epic did not have the generative parameters for: real procurement "
            "history, or a synthetic set generated from a materially different family.",
            "Real purchase history, where no generative truth exists and the only available "
            "check is out-of-sample calibration.",
        ),
        LimitationRecord(
            "L-2",
            "The far tail of every survival curve is extrapolation",
            f"The forward grid runs {horizon} days from the committed as-of date, past the "
            f"longest duration the input observes, so beyond that point the curve is the "
            f"fitted family's shape rather than anything the data supports. Shortening the "
            f"horizon is an E003 scope decision with its own reversal trigger and is not "
            f"E007's to take.",
            horizon_evidence,
            "`residual_tail_mass` exceeding a reported threshold on any line, or a "
            "planning-relevant percentile falling outside the grid — E003's own recorded "
            "reversal trigger for the horizon.",
            "A per-run horizon chosen from the fitted posterior, or a variable-resolution "
            "grid: daily for the first quarter, weekly thereafter.",
        ),
        LimitationRecord(
            "L-3",
            "The realized held-out uncensored event count does not support the registered "
            "coverage band's precision",
            f"A {manifest.held_out_fraction_declared:.2f} held-out split of this input "
            f"realizes **{events} gradeable events**. At that count an interval around a "
            f"coverage estimate is materially wider than the registered "
            f"{low:.0%}–{high:.0%} band implies. This epic publishes the realized count and "
            f"states the shortfall; it adjusts no band and asserts no coverage threshold of "
            f"its own (FR-026).",
            f"`specs/prd.md` states ~{REGISTERED_UNCENSORED_EVENT_ASSUMPTION} uncensored "
            f"events after splitting and derives its band from that figure; {events} is what "
            f"the split actually produces, recorded on this run as "
            f"`forecast_run.held_out_uncensored_event_count`.",
            "A held-out set whose realized uncensored event count supports the published "
            "band, or a cross-validated coverage estimate that does.",
            "At production volume a single split leaves enough events for the band to be "
            "measurable, and none of this arises.",
        ),
        LimitationRecord(
            "L-4",
            "No vendor-level claim is supported below the stated observation count",
            "Below the observation floor published in § Per-Vendor Shrinkage, a vendor's "
            "estimate is majority-borrowed from the population and this run makes no "
            "vendor-level claim at it. The floor is derived by the published rule — the "
            "smallest training-line count at which realized shrinkage reaches "
            f"{SHRINKAGE_SUPPORT_THRESHOLD:.2f} — and not chosen after the weights were seen.",
            f"Measured on this run: {floor_evidence}. The weights are this fit's own realized "
            f"ρ per vendor, stored in `forecast_run.vendor_shrinkage` as a median with an "
            f"interval, rather than the 0.22-at-n=5 figure E005's datasheet publishes — that "
            f"number is a property of that dataset's generative constants, not of this fit.",
            "More lines per vendor, or a vendor-level claim that survives at the realized "
            "shrinkage.",
            "A real vendor population, where every vendor has enough observations to stand "
            "on.",
        ),
    )


# ---------------------------------------------------------------------------
# Per-vendor shrinkage and its observation floor (T037 — FR-019, FR-020)
# ---------------------------------------------------------------------------


def vendor_claim_observation_floor(
    weights: Mapping[str, VendorShrinkage], training_line_counts: Mapping[str, int]
) -> int | None:
    """The observation count below which no vendor-level claim is supported.

    The published rule, applied to the realized weights: the **smallest**
    training-line count at which realized shrinkage reaches
    `SHRINKAGE_SUPPORT_THRESHOLD`. ρ is monotone increasing in the count, so the
    smallest qualifying observed count is the boundary and no interpolation is
    needed between the counts this roster happens to carry.

    `None` when no vendor reaches the threshold — which is a published miss, not
    an omission: it says that at this dataset's per-vendor volumes every estimate
    is majority-borrowed from the population.
    """
    _checked_pairing(weights, training_line_counts)
    qualifying = [
        int(training_line_counts[vendor])
        for vendor, weight in weights.items()
        if weight.median >= SHRINKAGE_SUPPORT_THRESHOLD
    ]
    return min(qualifying) if qualifying else None


def _checked_pairing(
    weights: Mapping[str, VendorShrinkage], training_line_counts: Mapping[str, int]
) -> None:
    """Every vendor with a weight has an observation count, and the reverse.

    FR-020 requires the two to be reported *beside* each other per vendor, so a
    vendor present in one mapping and absent from the other has no reportable
    unit at all — and silently dropping it is how the sparse vendor, the one the
    disclosure exists for, disappears from the table.
    """
    if not weights:
        raise ReportError(
            "no vendor shrinkage weights were passed; FR-019 records one per vendor "
            "including a vendor with no training line"
        )
    missing_counts = sorted(set(weights) - set(training_line_counts))
    missing_weights = sorted(set(training_line_counts) - set(weights))
    if missing_counts or missing_weights:
        raise ReportError(
            f"the shrinkage weights and the training counts name different vendors: "
            f"{missing_counts or '[]'} have no count and {missing_weights or '[]'} have no "
            f"weight. FR-020 pairs each weight with its own observation count, so a vendor "
            f"present in one and not the other cannot be reported at all"
        )


def _shrinkage_section(
    weights: Mapping[str, VendorShrinkage],
    training_line_counts: Mapping[str, int],
    observation_floor: int | None,
) -> list[str]:
    """FR-019 and FR-020 as one table: weight, count, criterion, verdict per vendor.

    The floor is stated **beside each vendor** rather than once for the
    population, which is FR-038's unit applied where FR-020 says it must be: a
    single population-level sentence leaves the reader to perform the comparison
    vendor by vendor, and that is the step a reader of a sparse-vendor estimate is
    least likely to perform and most consequential to skip.
    """
    criterion = (
        "no observed training-line count reaches it"
        if observation_floor is None
        else f"{observation_floor} training lines"
    )
    lines = [
        f"- **Credible level**: {VENDOR_SHRINKAGE_HDI_PROBABILITY:.2f} highest-density "
        f"interval. Stated rather than assumed — \"wider\" is undefined between intervals of "
        f"different mass.",
        f"- **Vendor-claim observation floor**: {criterion} — the smallest training-line "
        f"count at which realized shrinkage reaches {SHRINKAGE_SUPPORT_THRESHOLD:.2f}, "
        f"derived by the published rule from this run's realized weights and not chosen "
        f"after seeing them.",
        "",
        "| Vendor | Training lines | Realized shrinkage weight | Interval | "
        "Vendor-level claim |",
        "|---|---|---|---|---|",
    ]
    for vendor in sorted(weights):
        weight = weights[vendor]
        count = int(training_line_counts[vendor])
        supported = weight.median >= SHRINKAGE_SUPPORT_THRESHOLD
        verdict = "supported" if supported else "**not supported**"
        lines.append(
            f"| `{vendor}` | {count} | {weight.median:.3f} | "
            f"[{weight.hpdi_low:.3f}, {weight.hpdi_high:.3f}] | {verdict} at the floor above |"
        )
    lines += [
        "",
        "A weight is the share of a vendor's estimate that is that vendor's own data rather "
        "than the population's: ρⱼ = τ²/(τ² + σ²/nⱼ), a plug-in of two fitted scales, so it "
        "carries a posterior of its own and is published as a median with an interval rather "
        "than as a bare number.",
    ]
    return lines


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _identity_section(manifest: RunManifest) -> list[str]:
    return [
        f"- **Run identifier**: `{manifest.run_id}`",
        f"- **As-of date**: {manifest.as_of_date.isoformat()}",
        f"- **Code revision**: `{manifest.code_commit}`",
        f"- **Worktree state**: {'modified' if manifest.code_worktree_dirty else 'clean'}",
        f"- **Model version**: `{manifest.model_version}`",
        f"- **Artifact schema version**: {manifest.artifact_schema_version}",
        "- **Active pointer**: set explicitly on this run in a transaction of its own, after "
        "every artifact was durable — never implied by recency.",
    ]


def _provenance_section(manifest: RunManifest, fixture_agrees: bool) -> list[str]:
    """FR-045's two fields in the artifact a reader actually reads, plus the digests.

    SC-040 exists because the manifest is a database row and the reader reads
    this file: recording the layer and the datasheet where the reader does not
    look leaves FR-045 undischarged with every column present.
    """
    agreement = (
        "agrees with the digest E005 publishes for it"
        if fixture_agrees
        else "**differs from the digest E005 publishes** — a provenance warning, not a "
        "refusal: the rows this fit read are unchanged and only the chain back to the "
        "upstream artifact has broken"
    )
    return [
        f"- **Input layer**: `{manifest.input_layer}` — every number descending from this "
        f"run inherits that label.",
        f"- **Datasheet reference**: `{manifest.input_datasheet_ref}`",
        f"- **Input row hash**: `{manifest.input_data_hash}` — taken over the lines and "
        f"lifecycle events read from the schema, never over the committed file.",
        f"- **Serialization convention**: `{manifest.canonical_serialization}`",
        f"- **Fixture file digest**: `{manifest.input_fixture_digest}` — a second, distinct "
        f"value beside the row hash. Equal digests would mean the run hashed the file.",
        f"- **Fixture digest agreement**: {agreement}.",
        f"- **Roster hash**: `{manifest.roster_hash}`",
    ]


def _shape_section(manifest: RunManifest) -> list[str]:
    """The realized frame. **No verdict on any field here, deliberately.**

    The wall clock and the realized sampling shape are recorded and judged
    against nothing; attaching a verdict to either would manufacture a gate
    FR-026 forbids. The chain count is the one exception and it carries its
    criterion, because four chains is a published precondition the run had to
    clear before sampling.
    """
    return [
        f"- **Chains**: {manifest.chain_count}, against a published minimum of "
        f"{CHAINS_MIN} — precondition met before sampling began.",
        f"- **Draws per line**: {manifest.draw_count}",
        f"- **Tuning draws per chain**: {manifest.tuning_count}",
        f"- **Grid horizon**: {manifest.horizon_days} days, read from `schema_constants` "
        f"over the connection.",
        f"- **Draw serialization**: `{manifest.draw_serialization}`",
        f"- **Artifact hash**: `{manifest.artifact_hash.hex()}`",
        f"- **Wall clock**: {manifest.wall_clock_seconds:.1f} seconds — recorded, with no "
        f"criterion and therefore no verdict.",
        f"- **Sampling seed entropy**: `{manifest.seed_entropy}`",
    ]


def _split_section(manifest: RunManifest) -> list[str]:
    """The split's realized composition, and SC-025's event count with its verdict."""
    events = manifest.held_out_uncensored_event_count
    low, high = REGISTERED_COVERAGE_BAND
    supports = events >= REGISTERED_UNCENSORED_EVENT_ASSUMPTION
    disposition = (
        "supports" if supports else "**does not support**"
    )
    return [
        f"- **Declared held-out fraction**: {manifest.held_out_fraction_declared:.2f}, a "
        f"committed constant fixed before the split was drawn.",
        f"- **Realized held-out fraction**: {manifest.held_out_fraction_realized:.4f}",
        f"- **Split seed entropy**: `{manifest.split_seed_entropy}`",
        f"- **Split assignment hash**: `{manifest.split_assignment_hash}`",
        f"- **Training lines**: {manifest.training_line_count}",
        f"- **Open lines forecast**: {manifest.open_line_count}",
        f"- **Realized held-out uncensored event count**: {events}, against the "
        f"~{REGISTERED_UNCENSORED_EVENT_ASSUMPTION} events `specs/prd.md` derives its "
        f"registered {low:.0%}–{high:.0%} coverage band from — so the realized count "
        f"{disposition} the precision that band claims. The band is that document's and is "
        f"restated here; this run asserts no coverage threshold and no calibration verdict "
        f"of its own (FR-026). See limitation L-3.",
    ]


def _horizon_section(manifest: RunManifest, maximum_observed_days: int | None) -> list[str]:
    """FR-031's disclosure, with the maximum **computed** rather than asserted (G-10)."""
    if maximum_observed_days is None:
        return [
            f"- **Grid horizon**: {manifest.horizon_days} days",
            f"- **Maximum observed duration**: none — no line has delivered at "
            f"{manifest.as_of_date.isoformat()}.",
            f"- **Extrapolation beyond the observed maximum**: the whole "
            f"{manifest.horizon_days}-day grid, since the input observes no completed "
            f"duration to compare it against.",
        ]
    beyond = manifest.horizon_days - maximum_observed_days
    return [
        f"- **Grid horizon**: {manifest.horizon_days} days",
        f"- **Maximum observed duration**: {maximum_observed_days} days, computed from "
        f"`lifecycle_event` as `terminal.occurred_at::date − order_date` over the lines "
        f"delivered at the as-of date. The datasheet publishes a median and a P80 and never "
        f"a maximum, so this figure is measured here rather than quoted (G-10).",
        f"- **Extrapolation beyond the observed maximum**: {beyond} days — the horizon "
        f"{'extends past' if beyond > 0 else 'does not reach'} the longest duration the "
        f"input observes, so that span of every curve is the fitted family's shape. See "
        f"limitation L-2.",
    ]


def _emitted_set_section() -> list[str]:
    """FR-040's membership, stated in the artifact rather than only in a document."""
    lines = [
        "Exactly three report kinds are emitted by this epic, enumerated rather than left "
        "as a category:",
        "",
    ]
    lines += [f"- **Report kind** — {name}: {description}" for name, description in
              EMITTED_REPORT_KINDS]
    lines += ["", "- **This file**: run report."]
    return lines


def render_run_report(
    manifest: RunManifest,
    *,
    procurement_input: ProcurementInput,
    training_line_counts: Mapping[str, int],
    ablation: AblationOutcome,
    fixture_digest_agrees: bool = True,
) -> str:
    """The whole run report, as Markdown under the declared schema.

    Deterministic: no clock read and no environment read. Every figure comes from
    the manifest the same write stored or from the input rows the fit read, so the
    report and the run row cannot disagree about what happened.

    `ablation` is required and carries no default. A default would be a declared
    section rendered from something nobody measured, which is the placeholder the
    closed schema exists to exclude — and DV-020's report half is the assertion
    that the entry is there.
    """
    maximum_observed_days = maximum_observed_duration_days(procurement_input, manifest.as_of_date)
    weights = manifest.vendor_shrinkage
    _checked_pairing(weights, training_line_counts)
    observation_floor = vendor_claim_observation_floor(weights, training_line_counts)
    records = limitations(
        manifest,
        maximum_observed_days=maximum_observed_days,
        smallest_vendor_training_lines=min(int(count) for count in training_line_counts.values()),
        observation_floor=observation_floor,
    )
    check_limitations(records)

    bodies: dict[str, list[str]] = {
        "Run Identity": _identity_section(manifest),
        "Input Provenance": _provenance_section(manifest, fixture_digest_agrees),
        "Sampling Shape": _shape_section(manifest),
        "Split and Held-Out Evidence": _split_section(manifest),
        "Censoring Ablation": _ablation_section(ablation),
        "Per-Vendor Shrinkage": _shrinkage_section(
            weights, training_line_counts, observation_floor
        ),
        "Horizon and Extrapolation": _horizon_section(manifest, maximum_observed_days),
        "Limitations": _limitations_section(records),
        "Emitted Report Set": _emitted_set_section(),
    }
    missing = [title for title in SECTION_TITLES if title not in bodies]
    if missing:
        raise ReportError(
            f"the declared schema names section(s) {missing} that this renderer does not "
            f"emit; a declared section with no body is a field a reader is owed and does "
            f"not get"
        )

    parts: list[str] = [
        "# Forecast Run Report",
        "",
        f"Run `{manifest.run_id}` at as-of date {manifest.as_of_date.isoformat()}. Every "
        f"field below belongs to this report's declared schema; nothing here is a coverage "
        f"threshold, a calibration verdict, or a judgement on forecast quality — those are "
        f"the evaluation harness's and are deliberately absent (FR-026).",
        "",
    ]
    for ordinal, title in enumerate(SECTION_TITLES, start=1):
        parts += [f"## {ordinal}. {title}", "", *bodies[title], ""]
    return "\n".join(parts).rstrip("\n") + "\n"


def _limitations_section(records: Sequence[LimitationRecord]) -> list[str]:
    """Each record's four parts, one heading per record — `datasheet.py`'s shape."""
    lines = [
        f"{len(records)} records, each in four parts (FR-027): scope decision, supporting "
        f"evidence, reversal trigger, production-scale alternative.",
        "",
    ]
    for record in records:
        lines += [
            f"### {record.identifier} — {record.subject}",
            "",
            f"- **Identifier**: `{record.identifier}`",
            f"- **Subject**: {record.subject}",
            f"- **Scope decision**: {record.scope_decision}",
            f"- **Supporting evidence**: {record.supporting_evidence}",
            f"- **Reversal trigger**: {record.reversal_trigger}",
            f"- **Production-scale alternative**: {record.production_scale_alternative}",
            "",
        ]
    return lines


def write_run_report(
    manifest: RunManifest,
    *,
    procurement_input: ProcurementInput,
    training_line_counts: Mapping[str, int],
    ablation: AblationOutcome,
    fixture_digest_agrees: bool = True,
    report_root: Path | str | None = None,
) -> Path:
    """Render the report and write it to `paths.run_report_path(run_id)`.

    Named by `run_id` because a shipped run has one and it is the identifier the
    job's single stdout line carries (FR-039): a reader holding it reaches the
    report without consulting an index.
    """
    text = render_run_report(
        manifest,
        procurement_input=procurement_input,
        training_line_counts=training_line_counts,
        ablation=ablation,
        fixture_digest_agrees=fixture_digest_agrees,
    )
    target = run_report_path(manifest.run_id, report_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    return target


# ---------------------------------------------------------------------------
# The refusal report (T083 — FR-017, FR-037, FR-038, FR-040)
# ---------------------------------------------------------------------------

#: The refusal report's closed schema. All four sections are rendered on every
#: refusal, including the one that does not apply: a report whose "Unmet
#: Preconditions" section is absent and a report whose preconditions were all
#: met are indistinguishable to a reader, and the difference between a
#: pre-sampling and a post-sampling refusal is the whole of what this file is
#: read for.
REFUSAL_SECTION_TITLES: tuple[str, ...] = (
    "Refused Attempt",
    "Sampling",
    "Unmet Preconditions",
    "Breached Blocking Diagnostics",
)

#: FR-017's field set for a breached blocking diagnostic, as the labels this
#: report renders them under. Five fields plus the verdict, which is FR-038's
#: unit with the refusal itself as the verdict. `Parameter` appears only where
#: the metric is parameter-scoped — `divergent_transitions` is measured over the
#: run and has none, and rendering an empty one would be a field with no value.
REFUSAL_DIAGNOSTIC_FIELDS: tuple[str, ...] = (
    "Metric",
    "Parameter",
    "Realized value",
    "Threshold",
    "Threshold direction",
    "Verdict",
)

#: The **two**-field set a pre-sampling refusal carries, and no threshold
#: direction: a precondition is not a measured metric, so there is no floor or
#: ceiling for a direction to disambiguate (FR-017's trailing clause, FR-035).
REFUSAL_PRECONDITION_FIELDS: tuple[str, ...] = ("Precondition", "Realized value", "Verdict")

#: What the Sampling section says when the refusal came before the sampler ran.
#: FR-037 requires the report to record this rather than leave the section
#: empty: FR-035's observable evidence is that *nothing was sampled*, which is a
#: different fact from FR-017's *nothing was written*, and a reader cannot tell
#: them apart from an absent section.
NOTHING_SAMPLED = "nothing was sampled"

#: The five stores a refusal leaves as it found them, enumerated in the report
#: rather than described (SC-015). Named here so the file states its own
#: guarantee to a reader who is holding it precisely because no row exists.
UNTOUCHED_STORES: tuple[str, ...] = (
    "forecast_run",
    "line_posterior",
    "held_out_prediction",
    "forecast_split_assignment",
    "forecast_diagnostic",
)


@dataclass(frozen=True, slots=True)
class SampledShape:
    """The shape a refused attempt actually reached before it was refused.

    Recorded rather than quoted from the published constants: FR-037 asks for
    the attempt's *realized* shape, and an attempt refused at two chains is
    precisely the case where the realized and the published shapes differ.
    """

    chain_count: int
    draws_per_chain: int
    tuning_draws_per_chain: int

    @property
    def draw_count(self) -> int:
        """Post-warmup draws in total — the product, never a fourth constant."""
        return self.chain_count * self.draws_per_chain


@dataclass(frozen=True, slots=True)
class UnmetPrecondition:
    """One pre-sampling precondition and the value that failed it.

    Two fields, deliberately. FR-017 fixes a five-field set for a *measured*
    diagnostic and a two-field set for a precondition, and the difference is not
    an omission: a precondition is knowable before sampling and has no realized
    threshold direction to report.
    """

    precondition: str
    realized_value: str


@dataclass(frozen=True, slots=True)
class RefusedAttempt:
    """Everything one refused attempt leaves behind, in one record.

    A record rather than eight arguments, because FR-037 makes the identifier a
    function of three of these fields and the report's content a function of the
    rest — and a caller able to supply the reason without the evidence would
    eventually emit a file that records that a run refused and not why.
    """

    as_of_date: date
    input_data_hash: str
    attempted_at: datetime
    reason: str
    wall_clock_seconds: float
    sampled_shape: SampledShape | None = None
    preconditions: tuple[UnmetPrecondition, ...] = ()
    diagnostics: tuple[DiagnosticRow, ...] = field(default=())

    @property
    def attempt_id(self) -> str:
        """This attempt's identity, from `paths.py` and never re-derived here."""
        return refused_attempt_id(self.as_of_date, self.input_data_hash, self.attempted_at)


def _refused_attempt_section(attempt: RefusedAttempt) -> list[str]:
    """Which input, at which anchor, at which instant, and why."""
    return [
        f"- **Attempt identifier**: `{attempt.attempt_id}`",
        f"- **As-of date**: {attempt.as_of_date.isoformat()}",
        f"- **Input row hash**: `{attempt.input_data_hash}` — the full digest, of which the "
        f"identifier above carries a prefix.",
        f"- **Attempted at**: {attempt.attempted_at.astimezone(UTC).isoformat()}",
        f"- **Reason**: {attempt.reason}",
        f"- **Stores left as found**: "
        f"{', '.join(f'`{store}`' for store in UNTOUCHED_STORES)}, and the active-run "
        f"pointer (SC-015). This file is not a write to any of them.",
    ]


def _refusal_sampling_section(attempt: RefusedAttempt) -> list[str]:
    """The realized shape, or the record that nothing was sampled at all.

    The wall clock carries no verdict, for the same reason the run report's does
    not: it is a measure with no decision criterion, and attaching a verdict to
    one would manufacture a gate FR-026 forbids.
    """
    clock = (
        f"- **Wall clock**: {attempt.wall_clock_seconds:.1f} seconds — recorded, with no "
        f"criterion and therefore no verdict."
    )
    shape = attempt.sampled_shape
    if shape is None:
        return [
            f"- **Realized sampling shape**: {NOTHING_SAMPLED}. The refusal is a "
            f"pre-sampling precondition, so there is no sampler output to inspect — which "
            f"is the evidence FR-035 leaves, and it differs from FR-017's, where a sample "
            f"was taken and nothing was written.",
            clock,
        ]
    return [
        f"- **Realized sampling shape**: {shape.chain_count} chains x "
        f"{shape.draws_per_chain} draws = {shape.draw_count} post-warmup draws, with "
        f"{shape.tuning_draws_per_chain} tuning draws per chain. A sample was taken and "
        f"nothing was written (FR-017).",
        clock,
    ]


def _refusal_precondition_section(attempt: RefusedAttempt) -> list[str]:
    """Each unmet precondition as its two-field set, plus the verdict."""
    if not attempt.preconditions:
        return [
            "None — every pre-sampling precondition was met, so this refusal followed "
            "sampling rather than preceding it."
        ]
    lines: list[str] = []
    for unmet in attempt.preconditions:
        lines += [
            f"- **Precondition**: {unmet.precondition}",
            f"- **Realized value**: {unmet.realized_value}",
            "- **Verdict**: **unmet** — refused before sampling, so nothing was sampled "
            "and nothing was written.",
            "",
        ]
    return lines


def _refusal_diagnostic_section(attempt: RefusedAttempt) -> list[str]:
    """**Every** breached blocking diagnostic, each as FR-017's five-field set.

    Every one and not the first: several rows can breach in a single run, and an
    operator handed one of them returns for a second run to discover the next.
    The `Parameter` field is rendered exactly where the metric is
    parameter-scoped, which is what FR-017 asks for — `r_hat`, `ess_bulk` and
    `ess_tail` are keyed by parameter and a bare metric name does not say which.
    """
    if not attempt.diagnostics:
        return [
            "None — no blocking diagnostic was breached, so this refusal preceded "
            "sampling rather than following it."
        ]
    lines = [
        f"{len(attempt.diagnostics)} breached blocking diagnostic(s), each with its "
        f"realized value, the threshold it was judged against and that threshold's "
        f"direction — a value and a bar do not resolve to a verdict without it.",
        "",
    ]
    for row in attempt.diagnostics:
        scope = f" on `{row.parameter_name}`" if row.parameter_name is not None else ""
        realized = (
            f"{row.observed_value:g}"
            if row.is_computable
            else f"`{row.observed_value}` — **uncomputable**, not out of range"
        )
        lines += [f"### {row.metric}{scope}", ""]
        lines.append(f"- **Metric**: `{row.metric}`")
        if row.parameter_name is not None:
            lines.append(f"- **Parameter**: `{row.parameter_name}`")
        lines += [
            f"- **Realized value**: {realized}",
            f"- **Threshold**: {row.threshold_value:g}",
            f"- **Threshold direction**: `{row.threshold_direction}` — "
            f"{direction_prose(row.threshold_direction)}.",
            "- **Verdict**: **breached** — the run is refused and no artifact is written.",
            "",
        ]
    return lines


def render_refusal_report(attempt: RefusedAttempt) -> str:
    """The whole refusal report, as Markdown under its declared schema.

    Deterministic: no clock read and no environment read. The instant is the
    attempt's own, passed in, so the file and the filename it is written under
    cannot describe two different moments.
    """
    bodies: dict[str, list[str]] = {
        "Refused Attempt": _refused_attempt_section(attempt),
        "Sampling": _refusal_sampling_section(attempt),
        "Unmet Preconditions": _refusal_precondition_section(attempt),
        "Breached Blocking Diagnostics": _refusal_diagnostic_section(attempt),
    }
    missing = [title for title in REFUSAL_SECTION_TITLES if title not in bodies]
    if missing:
        raise ReportError(
            f"the refusal report's declared schema names section(s) {missing} that this "
            f"renderer does not emit; a refusal is recorded by this file and the job's "
            f"standard error alone (G-8), so a missing section is evidence nobody has"
        )
    parts: list[str] = [
        "# Forecast Refusal Report",
        "",
        f"Attempt `{attempt.attempt_id}` was refused. No row was written in any of "
        f"{', '.join(f'`{store}`' for store in UNTOUCHED_STORES)} and the active-run "
        f"pointer is unmoved. A refused attempt has no `run_id`, because FR-017 forbids "
        f"writing the run row — this file and the job's standard error are the only "
        f"surviving record of why the run refused (G-8), and this file is the durable "
        f"half of that pair.",
        "",
    ]
    for ordinal, title in enumerate(REFUSAL_SECTION_TITLES, start=1):
        parts += [f"## {ordinal}. {title}", "", *bodies[title], ""]
    return "\n".join(parts).rstrip("\n") + "\n"


def write_refusal_report(
    attempt: RefusedAttempt, report_root: Path | str | None = None
) -> Path:
    """Emit one file per attempt, beside the run reports, never overwriting.

    `x` mode rather than `w`, so "never overwritten by a later refusal" is a
    mechanism rather than an argument from the timestamp's resolution: two
    attempts that somehow shared an identifier would refuse the second write
    rather than silently discard the first, and a retry loop is exactly when
    that history matters.
    """
    text = render_refusal_report(attempt)
    target = refusal_report_path(
        attempt.as_of_date, attempt.input_data_hash, attempt.attempted_at, report_root
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as handle:
            handle.write(text.encode("utf-8"))
    except FileExistsError as exc:
        raise ReportError(
            f"a refusal report already exists at {target}; FR-037 retains one file per "
            f"attempt and never overwrites, so the earlier attempt's evidence is kept and "
            f"this one is reported instead"
        ) from exc
    return target
