"""How long each transition takes, and how that spread decomposes.

`data-model.md` § Duration model and § Category tiers solve this; this module
implements the solution and **re-derives every constant it can** rather than
transcribing it. The distinction matters here more than elsewhere, because the
numbers form a chain — σ_w is back-solved from the product document's published
61-day median and 94-day P80, τ is 0.24 × σ_w, σ_r is √(σ_w² − σ_c²), and σ₀ is
solved from σ_r. A transcribed value anywhere in that chain is a number nobody
can attribute, and FR-008's band is asserted against the far end of it.

**Two distinctly named duration quantities** (FR-035), which must never be
written where the other is meant:

* `category_expected_duration_days` — `exp(μ_base + c_k + σ_w²/2)`, a property
  of the **category**. Intended: T1 ≈ 84.9, T2 ≈ 69.5, T3 ≈ 46.6.
* `line_expected_total_duration_days` — the same with the vendor offset `b_v`
  added, a property of the **line**. FR-011's need-by derivation uses this one.

**Offsets are additive on the log scale and applied to every transition**, so a
line's whole timeline scales by `exp(b_v + c_k)` and its aggregate log-duration
shifts by exactly `b_v + c_k`. Tier offsets are mean-zero at the declared line
weights — `(8×0.20 + 8×0.00 + 4×(−0.40)) / 20 = 0` — so a category term cannot
shift FR-007's aggregate target.

**The 1-day floor is load-bearing, not cosmetic.** A leg rounding to zero makes
`occurred_at` non-increasing, which the delivered schema rejects at load, long
after the artifact carrying it was committed. It is disclosed in the datasheet
for the same reason: it biases short legs upward and that bias is real.

**The aggregate target is an approximation, not an identity.** A sum of
independent lognormals is not lognormal. `T_PRE` is therefore *calibrated by
simulation* rather than solved in closed form, and `solve_pre_rework_mean()`
below is the calibration, kept in the module so the pinned constant is
reproducible instead of asserted.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np

from model.procurement.allocate import REWORK_MAX_LOOPS, rework_loop_allocation

__all__ = [
    "FORWARD_SHARES",
    "MU_BASE",
    "LOOP_SHARES",
    "REWORK_SHARES",
    "SIGMA_0",
    "SIGMA_C",
    "SIGMA_R",
    "SIGMA_W",
    "SPREAD_RATIO_BAND",
    "T_PRE",
    "TAU",
    "TIER_OFFSETS",
    "SpreadBandError",
    "SpreadDecomposition",
    "category_expected_duration_days",
    "check_spread_band",
    "decompose_spread",
    "draw_line_durations",
    "line_expected_total_duration_days",
    "solve_pre_rework_mean",
    "solve_sigma_0",
    "vendor_offsets",
]

#: FR-007's two published figures — the product document's own illustration of a
#: delivery distribution. Everything downstream is derived from this pair.
MEDIAN_TARGET_DAYS = 61.0
P80_TARGET_DAYS = 94.0

#: z at the 80th percentile of the standard normal, to the precision the
#: back-solve in `data-model.md` used.
_Z80 = 0.8416

#: Within-vendor log spread, back-solved: ln(94/61) / z₀.₈₀ = 0.4324 / 0.8416.
SIGMA_W = round(math.log(P80_TARGET_DAYS / MEDIAN_TARGET_DAYS) / _Z80, 2)

#: Between-vendor log spread. FR-008 targets a ratio of approximately 0.24, and
#: τ is that ratio times σ_w — which is the definition that makes the
#: *unadjusted* ratio exactly 0.24 by construction, and is the reason the
#: *category-adjusted* ratio FR-036 requires be asserted comes out at ≈0.2657
#: instead (analysis finding A-020, carried open).
UNADJUSTED_RATIO_TARGET = 0.24
TAU = round(UNADJUSTED_RATIO_TARGET * SIGMA_W, 4)

#: Category log spread, from the tier offsets at their line weights.
SIGMA_C = 0.219

#: FR-008's inclusive band on the spread ratio. The **category-adjusted** ratio
#: is what is asserted against it (FR-036); the unadjusted one is recorded.
SPREAD_RATIO_BAND = (0.12, 0.49)

#: Residual log spread — σ_w net of the category component.
SIGMA_R = math.sqrt(SIGMA_W**2 - SIGMA_C**2)

#: Log-scale base. `exp(MU_BASE)` is FR-007's median target, which is what makes
#: the three intended category expected durations fall out rather than being
#: chosen: 61 × e^{σ_w²/2} = 69.5 at tier 2, times e^{±c_k} for the others.
MU_BASE = math.log(MEDIAN_TARGET_DAYS)

#: Share of the pre-rework aggregate mean carried by each forward transition,
#: in lifecycle order: submitted→under_review, →approved,
#: →released_for_fabrication, →shipped, →delivered. Sums to one.
FORWARD_SHARES = (0.12, 0.20, 0.08, 0.46, 0.14)

#: The two shares `data-model.md` names as rework legs:
#: `under_review → revise_and_resubmit` and `revise_and_resubmit → submitted`.
#: Deliberately **not** part of the apportionment above — rework is additional
#: time, not a redistribution of it.
REWORK_SHARES = (0.16, 0.12)

#: **A loop is three transitions, not two.** `state_sequence` re-visits three
#: states per loop — `revise_and_resubmit`, `submitted`, `under_review` — so
#: getting back to where the loop started costs a third leg:
#: `submitted → under_review` again, at the forward share it always carries.
#:
#: The two named rework shares alone give `5 + 2L` legs against `6 + 3L` events,
#: and the mismatch is silent in every unit test: a rework line simply runs out
#: of dates one event short and is recorded as censored. It surfaced only in the
#: end-to-end run, where the emitted delivered share was 0.618 while the shape
#: gate — computing delivery from the same short leg list — reported 0.874. Two
#: definitions of one fact, agreeing with each other and with nothing else.
LOOP_SHARES = (*REWORK_SHARES, FORWARD_SHARES[0])

_TIER_1 = (
    "GENERATOR_ASSEMBLY",
    "LIQUID_FILLED_TRANSFORMER",
    "MEDIUM_VOLTAGE_SWITCHGEAR",
    "PRIMARY_UNIT_SUBSTATION",
    "SECONDARY_UNIT_SUBSTATION",
    "WATER_CHILLER",
    "COOLING_TOWER",
    "HEATING_BOILER",
)
_TIER_2 = (
    "AUTOMATIC_TRANSFER_SWITCH",
    "COMPUTER_ROOM_AIR_CONDITIONER",
    "ENERGY_RECOVERY_UNIT",
    "LOW_VOLTAGE_SWITCHGEAR",
    "PAD_MOUNTED_TRANSFORMER",
    "STATIC_UNINTERRUPTIBLE_POWER_SUPPLY",
    "SWITCHBOARD",
    "VARIABLE_FREQUENCY_DRIVE",
)
_TIER_3 = (
    "CIRCUIT_PROTECTIVE_DEVICE",
    "HYDRONIC_PUMP",
    "LOW_VOLTAGE_TRANSFORMER",
    "MEDIUM_VOLTAGE_CABLE",
)

#: Log offset per material category. Mean-zero at the declared line weights.
TIER_OFFSETS: Mapping[str, float] = MappingProxyType(
    {c: 0.20 for c in _TIER_1} | {c: 0.00 for c in _TIER_2} | {c: -0.40 for c in _TIER_3}
)


def solve_sigma_0() -> float:
    """Per-transition log scale, from `(e^{σ₀²} − 1)·Σwₜ² = e^{σ_r²} − 1`.

    The equation matches the coefficient of variation of a sum of independent
    lognormals with the declared shares against the residual spread: the sum's
    variance is `Σ(wₜT)²(e^{σ₀²} − 1)` at mean `T`, so `CV² = (e^{σ₀²} − 1)Σwₜ²`.
    Residual rather than within-vendor, because the vendor and category offsets
    contribute their own spread on top of the draw noise.
    """
    sum_of_squares = sum(w * w for w in FORWARD_SHARES)
    return math.sqrt(math.log((math.exp(SIGMA_R**2) - 1) / sum_of_squares + 1))


SIGMA_0 = round(solve_sigma_0(), 2)

#: Pre-rework aggregate mean, **calibrated** by `solve_pre_rework_mean()` so the
#: rework-inclusive realized median and P80 land on SC-023's targets. Fitting
#: before rework instead lands near 66 / 104 (STF-005), which is why this is not
#: simply the median target.
#:
#: **Calibrated against the realized 199-line dataset, not a converged one.**
#: `data-model.md` says T_pre is "solved so the rework-inclusive **realized**
#: median is 61 ± 5", and the realized population is the 199 lines actually
#: emitted. `solve_pre_rework_mean` bisects over 20,000 lines instead, where the
#: median has converged, and returns 57.8 — at which the *emitted* dataset has a
#: median of exactly 56.0. That satisfies SC-023 with **zero margin**, which is
#: not a calibration so much as a coincidence that happens to round the right way.
#:
#: 60.0 is the value with the widest margin on the emitted dataset **across all
#: three constraints jointly**, which is the part that took a second pass. Tuned
#: against SC-023 alone the answer is 62.0 — and 62.0 breaks FR-010, because
#: longer durations censor fewer lines and `revise_and_resubmit` empties out.
#: The constants interact, so the search has to be joint: median 58 (2 days of
#: room), P80 90.4 (4.4), delivered share 0.879 inside `[0.804, 0.90]` with room
#: at both ends, late share 0.263 inside FR-011's band. Every one of those is
#: recorded in the datasheet against its bounding criterion.
#:
#: Keeping both numbers is deliberate. The converged solve is the honest answer
#: to "what mean produces a 61-day median in the limit"; 62.0 is the honest
#: answer to "what mean makes *this* dataset satisfy SC-023 with room". They
#: differ by ~7% because the median over 199 lines moves several days between
#: seeds — the sampling noise recorded against the SC-023 property test.
#:
#: Earlier corrections, kept because each was a real defect: an even 0–3 loop
#: spread is 3.6× the declared rework and pulled the answer to 46.7; and before
#: `LOOP_SHARES` fixed the loop from two legs to three, the calibration was
#: fitting a population whose rework lines were each one leg short.
T_PRE = 60.0


def vendor_offsets(vendor_ids: Sequence[str]) -> Mapping[str, float]:
    """Mean-zero log offsets across vendors, with population spread exactly τ.

    Deterministic and **not drawn**: the offsets are placed on evenly-spaced
    normal quantiles and then standardised. Drawing them would make the realized
    between-vendor spread a property of the seed, so FR-008's band would be
    asserted against a number the generator did not control — and the whole
    point of FR-008 is that the spread is induced deliberately.

    Standardising with `ddof=0` is what makes the realized population spread
    equal τ exactly rather than approximately, so `decompose_spread` measures
    what was intended.
    """
    count = len(vendor_ids)
    if count < 2:
        raise ValueError(f"a between-vendor spread needs at least two vendors, found {count}")
    quantiles = np.array(
        [_inverse_normal_cdf((i + 0.5) / count) for i in range(count)], dtype=float
    )
    quantiles -= quantiles.mean()
    quantiles /= quantiles.std(ddof=0)
    return MappingProxyType(
        dict(zip(sorted(vendor_ids), (TAU * q for q in quantiles), strict=True))
    )


def _inverse_normal_cdf(p: float) -> float:
    """Φ⁻¹ via the error function — no SciPy dependency for one call."""
    return math.sqrt(2.0) * _erfinv(2.0 * p - 1.0)


def _erfinv(y: float) -> float:
    """Newton refinement of a rational first guess. Accurate well past need."""
    if abs(y) >= 1.0:  # pragma: no cover — quantiles are strictly interior
        raise ValueError(f"erfinv is undefined at {y}")
    a = 0.147
    ln1my2 = math.log(1.0 - y * y)
    term = 2.0 / (math.pi * a) + ln1my2 / 2.0
    x = math.copysign(math.sqrt(math.sqrt(term * term - ln1my2 / a) - term), y)
    for _ in range(3):
        err = math.erf(x) - y
        x -= err / (2.0 / math.sqrt(math.pi) * math.exp(-x * x))
    return x


def category_expected_duration_days(material_category: str) -> float:
    """`exp(μ_base + c_k + σ_w²/2)` — a property of the **category** (FR-035).

    Not to be confused with `line_expected_total_duration_days`, which adds the
    vendor offset. FR-035 requires the two be named distinctly wherever both
    appear, because they differ by a factor a reader cannot see.
    """
    if material_category not in TIER_OFFSETS:
        raise KeyError(
            f"{material_category!r} is not one of the 20 categories in the committed map"
        )
    return math.exp(MU_BASE + TIER_OFFSETS[material_category] + SIGMA_W**2 / 2)


def line_expected_total_duration_days(material_category: str, vendor_offset: float) -> float:
    """`exp(μ_base + c_k + b_v + σ_w²/2)` — a property of the **line** (FR-011)."""
    return category_expected_duration_days(material_category) * math.exp(vendor_offset)


def draw_line_durations(
    generator: np.random.Generator, log_offset: float, rework_loops: int
) -> list[int]:
    """Whole-day durations for one line's legs: five forward, plus two per loop.

    Returned in emission order — the five forward legs, then each loop's two
    rework legs — so the caller can walk them against the state machine without
    re-deriving which leg is which.
    """
    if rework_loops < 0:
        raise ValueError(f"rework loops cannot be negative, found {rework_loops}")
    shares = list(FORWARD_SHARES) + list(LOOP_SHARES) * rework_loops
    return [_draw_leg(generator, share, log_offset) for share in shares]


def _draw_leg(generator: np.random.Generator, share: float, log_offset: float) -> int:
    """One leg: lognormal at the apportioned mean, whole days, floored at one.

    `− σ₀²/2` puts the *mean* at `share × T_PRE`; without it the apportionment
    would set the median instead and the aggregate would overshoot by the
    lognormal's own skew.
    """
    mu = math.log(share * T_PRE) - SIGMA_0**2 / 2 + log_offset
    return max(1, round(float(generator.lognormal(mu, SIGMA_0))))


@dataclass(frozen=True, slots=True)
class SpreadDecomposition:
    """FR-036's three components and the two ratios computed from them.

    `vendor_variance` is **net of category** — the component measured after the
    category term is removed. `unadjusted_vendor_variance` is the naive one-way
    figure that ignores category entirely, carried as its own field rather than
    recomputed from the others: deriving the "unadjusted" ratio from the
    adjusted component would make the two ratios differ only by a denominator,
    and the whole point of reporting both is that the *numerators* differ when
    a vendor's apparent variability is really its category mix.
    """

    vendor_variance: float
    category_variance: float
    residual_variance: float
    unadjusted_vendor_variance: float

    @property
    def unadjusted_ratio(self) -> float:
        """√vendor / √(everything else), with category left confounded in.

        This is the figure FR-008's band would be checked against if nobody had
        asked where the between-vendor variation came from. Recorded beside the
        adjusted one so the gap between them is visible.
        """
        within = (
            self.vendor_variance
            + self.category_variance
            + self.residual_variance
            - self.unadjusted_vendor_variance
        )
        return (
            math.sqrt(self.unadjusted_vendor_variance) / math.sqrt(within)
            if within > 0
            else math.inf
        )

    @property
    def adjusted_ratio(self) -> float:
        """√vendor / √residual — **the ratio FR-036 asserts against the band**.

        Net of the category component, so a ratio that reaches the band because
        a vendor happens to carry long-lead categories is not counted as
        between-vendor heterogeneity.
        """
        return (
            math.sqrt(self.vendor_variance) / math.sqrt(self.residual_variance)
            if self.residual_variance > 0
            else math.inf
        )


class SpreadBandError(ValueError):
    """Raised when the category-adjusted spread ratio falls outside FR-008's band.

    A distinct type rather than a bare `ValueError`, because this is the one
    failure the caller is expected to surface as a *run* failure rather than
    handle: FR-036 requires the generator to fail rather than report the
    decomposition and pass.
    """


def check_spread_band(decomposition: SpreadDecomposition) -> None:
    """Enforce FR-008's band against the **category-adjusted** ratio (FR-036).

    The requirement is unusually explicit about the failure it wants, and about
    why reporting is not enough: *"Where the unadjusted ratio falls inside the
    band and the category-adjusted ratio does not, the run MUST fail rather than
    report the decomposition and pass."* Vendors do not supply identical
    category mixes, so a between-vendor mean difference driven by which
    categories a vendor happens to carry is exactly the wrong reason for the
    band to be met — and a decomposition that is printed but bounded nowhere
    leaves that outcome visible and still passing.

    The message names both ratios, because the interesting case is the one where
    they disagree and a reader needs to see the disagreement to act on it.
    """
    low, high = SPREAD_RATIO_BAND
    adjusted = decomposition.adjusted_ratio
    if low <= adjusted <= high:
        return
    unadjusted = decomposition.unadjusted_ratio
    inside_unadjusted = low <= unadjusted <= high
    raise SpreadBandError(
        f"the category-adjusted spread ratio is {adjusted:.4f}, outside FR-008's "
        f"[{low}, {high}] band"
        + (
            f" — while the unadjusted ratio {unadjusted:.4f} is inside it. The band is "
            f"met only because of category heterogeneity, which is the outcome FR-036 "
            f"requires this run to fail on rather than report"
            if inside_unadjusted
            else f" (the unadjusted ratio is {unadjusted:.4f})"
        )
        + f". Components: vendor {decomposition.vendor_variance:.6f}, category "
        f"{decomposition.category_variance:.6f}, residual "
        f"{decomposition.residual_variance:.6f}"
    )


def decompose_spread(
    log_durations: Sequence[float],
    vendor_ids: Sequence[str],
    material_categories: Sequence[str],
) -> SpreadDecomposition:
    """Split the variance of log duration into category, vendor and residual.

    **Sequential (order-of-entry) sums of squares, category entered first.**
    FR-036 names no estimator — that is analysis finding A-021 — and with an
    unbalanced cross-tab the order decides where the shared vendor/category
    variation lands. The requirement's own words settle it: the ratio asserted
    against the band must be *"the vendor component taken net of the
    material-category component"*, and net-of-category means category is removed
    before the vendor component is measured.

    The direction matters and is easy to get backwards. Entering **vendor**
    first credits confounded variation to the vendor, inflating the vendor
    component and making the band easier to pass — which is precisely the
    outcome FR-036 exists to prevent, since a vendor that looks variable only
    because it happens to carry long-lead categories would be counted as
    between-vendor heterogeneity. Category-first is therefore both what the
    requirement says and the conservative reading of it.

    The three components sum to total variance by construction, which is what
    the identity test checks.
    """
    values = np.asarray(log_durations, dtype=float)
    if values.size == 0:
        raise ValueError("cannot decompose the spread of an empty population")
    if not (values.size == len(vendor_ids) == len(material_categories)):
        raise ValueError(
            f"decomposition needs one vendor and one category per observation; got "
            f"{values.size} durations, {len(vendor_ids)} vendors, "
            f"{len(material_categories)} categories"
        )

    grand = float(values.mean())
    total = float(values.var(ddof=0))

    category_component = _between_group_variance(values, material_categories, grand)

    # The vendor component is measured on the category residuals, so shared
    # vendor/category variation has already been attributed to the category and
    # is not credited to the vendor. This is what "net of the material-category
    # component" means operationally.
    category_fitted = _group_means_broadcast(values, material_categories)
    residual_after_category = values - category_fitted + grand
    vendor_component = _between_group_variance(residual_after_category, vendor_ids, grand)

    residual_component = max(0.0, total - vendor_component - category_component)
    unadjusted_vendor = _between_group_variance(values, vendor_ids, grand)
    return SpreadDecomposition(
        vendor_component, category_component, residual_component, unadjusted_vendor
    )


def _group_means_broadcast(values: np.ndarray, labels: Sequence[str]) -> np.ndarray:
    means = {}
    for label in set(labels):
        mask = np.fromiter((x == label for x in labels), dtype=bool, count=len(labels))
        means[label] = float(values[mask].mean())
    return np.fromiter((means[x] for x in labels), dtype=float, count=len(labels))


def _between_group_variance(values: np.ndarray, labels: Sequence[str], grand: float) -> float:
    """Σ nᵍ (mean_g − grand)² / N — the group sum of squares as a variance."""
    total = 0.0
    for label in sorted(set(labels)):
        mask = np.fromiter((x == label for x in labels), dtype=bool, count=len(labels))
        count = int(mask.sum())
        total += count * (float(values[mask].mean()) - grand) ** 2
    return total / values.size


def solve_pre_rework_mean(
    line_count: int = 20_000, seed: int = 7, tolerance: float = 0.05
) -> float:
    """Calibrate `T_PRE` so the rework-inclusive aggregate hits SC-023's targets.

    By simulation rather than in closed form, because a sum of independent
    lognormals is not lognormal and the rework legs are conditional on a loop
    count. Bisection on the realized median, over a population large enough that
    the statistic has converged — at the delivered 199 lines the median moves by
    several days between seeds, so calibrating there would fit the noise.

    Kept in the module rather than done once in a scratch script so `T_PRE` is
    *reproducible*: a pinned constant whose derivation lives nowhere is exactly
    the unattributable number this epic keeps finding.
    """
    loops = np.array(rework_loop_allocation(line_count))
    categories = sorted(TIER_OFFSETS)
    vendors = vendor_offsets(tuple(f"V{i:02d}" for i in range(12)))
    offsets = np.array(
        [
            list(vendors.values())[i % 12] + TIER_OFFSETS[categories[i % 20]]
            for i in range(line_count)
        ]
    )

    def median_at(candidate: float) -> float:
        generator = np.random.default_rng(seed)
        shares = np.array(FORWARD_SHARES)
        base = np.log(shares * candidate) - SIGMA_0**2 / 2
        totals = np.zeros(line_count)
        for mu in base:
            totals += np.maximum(1, np.round(generator.lognormal(mu + offsets, SIGMA_0)))
        rework_base = np.log(np.array(LOOP_SHARES) * candidate) - SIGMA_0**2 / 2
        for loop in range(1, REWORK_MAX_LOOPS + 1):
            active = loops >= loop
            for mu in rework_base:
                drawn = np.maximum(1, np.round(generator.lognormal(mu + offsets, SIGMA_0)))
                totals += np.where(active, drawn, 0)
        return float(np.median(totals))

    low, high = 20.0, 200.0
    for _ in range(60):
        mid = (low + high) / 2
        if median_at(mid) < MEDIAN_TARGET_DAYS:
            low = mid
        else:
            high = mid
        if high - low < tolerance:
            break
    return round((low + high) / 2, 1)
