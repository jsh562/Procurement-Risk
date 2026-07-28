"""The per-vendor shrinkage weight ρⱼ = τ²/(τ² + σ²/nⱼ), with its own interval.

FR-019. ρ is a plug-in of two *fitted* scales, so it carries a posterior of its
own and is published as `{median, hpdi_low, hpdi_high}` — the shape
`fn_vendor_shrinkage_wellformed` validates, and what Principle II requires of a
number reported to a reader exactly where the uncertainty is largest.

Written as `n·τ²/(n·τ² + σ²)` so a vendor with **no training line** is
arithmetic rather than a special case: every draw of ρ is then exactly 0, the
honest triple is `(0, 0, 0)`, and it is published rather than omitted. A missing
vendor reads as an oversight, and `[0, 1]` would claim the fit cannot tell how
much of that vendor's estimate is its own data when the answer is none of it.

**The second quantity here is the vendor *effect* interval** — the posterior of
θⱼ, the vendor's own offset, whose spread is `sd(θⱼ|data) = τσ/√(nτ² + σ²)`.
SC-005 and DV-010 are claims about *that* interval and not about the ρ triple,
and the difference is measured rather than stylistic: ρ's own interval is
widest where the median weight is nearest 0.5 and collapses at both ends, so
"wider at the sparser vendor" is false of it for some rosters, while the
vendor-effect interval is strictly narrower at every extra training line for
every draw of the two scales. Both functions take **the same two draw
sequences**, so a caller holding a run's fitted τ and σ gets the weight and the
effect interval off one posterior rather than off a stand-in.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = [
    "ShrinkageError",
    "VendorEffect",
    "VendorShrinkage",
    "vendor_effect_interval",
    "vendor_effect_spread",
    "vendor_shrinkage",
]


class ShrinkageError(ValueError):
    """Raised when a roster or a posterior cannot yield a weight per vendor.

    A `ValueError`: every case is something the caller handed over — a negative
    cardinality, an empty roster, two parameter sequences that are not paired,
    or a credible level that is not one.
    """


@dataclass(frozen=True, slots=True)
class VendorShrinkage:
    """One vendor's weight, as the three numbers migration `0300` validates.

    A triple rather than a bare number. An earlier revision stored the point
    estimate alone, which is the shape Principle II refuses: ρ is uncertain
    precisely at the sparse-vendor end where a reader leans on it hardest.
    """

    median: float
    hpdi_low: float
    hpdi_high: float


@dataclass(frozen=True, slots=True)
class VendorEffect:
    """One vendor's effect interval, and the spread the interval came from.

    `spread_median` is the posterior median of `sd(θⱼ|data)`; `hpdi_low` and
    `hpdi_high` bound θⱼ itself at the stated credible level. Both are published
    because they answer different questions — the spread is the quantity DV-010
    is written in terms of, the interval is what a reader compares between two
    vendors — and reporting one without the other would leave the comparison
    resting on a number nothing else in the run states.
    """

    spread_median: float
    hpdi_low: float
    hpdi_high: float

    @property
    def width(self) -> float:
        """`hpdi_high − hpdi_low`, the quantity SC-005's comparison ranges over."""
        return self.hpdi_high - self.hpdi_low


def _paired_scales(
    tau_draws: ArrayLike, sigma_draws: ArrayLike
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """The two posterior sequences, proved paired and inside their support.

    Paired rather than pooled: ρ is a plug-in *per draw*, so element `i` of one
    sequence belongs with element `i` of the other. NumPy would broadcast a
    length-1 sequence against any other and raise on every other mismatch, which
    leaves exactly one silent case — every vendor conditioned on a single draw.
    """
    tau = np.asarray(tau_draws, dtype=float)
    sigma = np.asarray(sigma_draws, dtype=float)

    if tau.ndim != 1 or sigma.ndim != 1:
        raise ShrinkageError(
            f"each posterior sequence is one vector of draws, found shapes {tau.shape} and "
            f"{sigma.shape}; a chain-by-draw frame must be flattened by its caller, which is "
            f"the only place that knows whether the chains were meant to be pooled"
        )
    if tau.size == 0:
        raise ShrinkageError(
            "no posterior draws were passed; a weight summarised over an empty sequence has "
            "no median and no interval, and NumPy would report both as NaN"
        )
    if tau.shape != sigma.shape:
        raise ShrinkageError(
            f"the two posterior sequences must be the same length, found {tau.size} draws of "
            f"τ against {sigma.size} of σ; ρ is a plug-in per draw, so an unpaired sequence "
            f"would combine parameters that never appeared together in the fit"
        )
    if not np.all(np.isfinite(tau)) or np.any(tau < 0.0):
        raise ShrinkageError(
            "the between-vendor spread must be finite and non-negative; τ is a scale, and a "
            "negative draw of one never came from a sampler"
        )
    if not np.all(np.isfinite(sigma)) or np.any(sigma <= 0.0):
        raise ShrinkageError(
            "the residual scale must be finite and strictly positive; σ of zero makes every "
            "weight 1 by division-free accident rather than by fit, and a negative one "
            "inverts the ratio"
        )
    return tau, sigma


def _checked_roster(training_line_counts: Mapping[str, int]) -> None:
    """The roster, before a single weight is computed.

    An empty roster is refused because it is the one value that satisfies "every
    vendor asked about came back" vacuously, and a negative count because at
    `n = −1` the plug-in is a finite number outside `[0,1]` for most draws and a
    division by zero for one of them — both rejected long after the run.
    """
    if not isinstance(training_line_counts, Mapping):
        raise ShrinkageError(
            f"the training counts are a mapping of vendor to cardinality, found "
            f"{type(training_line_counts).__name__}; the keys are the roster, and a bare "
            f"sequence would publish weights nothing could be keyed to"
        )
    if not training_line_counts:
        raise ShrinkageError(
            "the roster is empty; `forecast_run.vendor_shrinkage` is NOT NULL with a shape "
            "helper that requires at least one member, and an empty object would satisfy "
            "the membership check by asking about nobody"
        )
    for vendor, count in training_line_counts.items():
        if isinstance(count, bool) or not isinstance(count, int | np.integer):
            raise ShrinkageError(
                f"{vendor}'s training count is a cardinality, found "
                f"{type(count).__name__}; a fractional count of lines is not a count"
            )
        if count < 0:
            raise ShrinkageError(
                f"{vendor} was passed {count} training lines; a count is a cardinality, and "
                f"a negative one makes ρ leave the unit interval silently"
            )


def _checked_level(hdi_probability: float) -> float:
    """The credible level, which SC-005 requires to be stated rather than assumed.

    "Wider" is undefined between intervals of different mass, and A-027 records
    that no requirement fixes a level — so there is no default to fall back on.
    A level of 0 is a point and a level of 1 is the whole support; both satisfy
    `hpdi_low <= median <= hpdi_high`, so the shape check would accept either.
    """
    if isinstance(hdi_probability, bool) or not isinstance(hdi_probability, float | int):
        raise ShrinkageError(
            f"the credible level is a probability, found {type(hdi_probability).__name__}"
        )
    level = float(hdi_probability)
    if not math.isfinite(level) or not 0.0 < level < 1.0:
        raise ShrinkageError(
            f"the credible level must lie strictly inside `(0, 1)`, found {level}; a level of "
            f"zero is a point and a level of one is the whole support, and neither is an "
            f"interval a reader can act on"
        )
    return level


def _nearest_rank_median(ordered: NDArray[np.float64]) -> float:
    """`ordered[ceil(0.5·n)]`, one-based — `schema_constants.percentile_convention`.

    Nearest rank without interpolation, so the published median is a draw the
    sampler actually produced rather than the midpoint of two it did not.
    """
    return float(ordered[max(math.ceil(0.5 * ordered.size), 1) - 1])


def _hpdi(ordered: NDArray[np.float64], level: float) -> tuple[float, float]:
    """The narrowest interval of the sorted draws holding at least `level` of them.

    The highest-density interval rather than a central one: ρ is skewed and
    frequently piles against 0 or 1, where an equal-tailed interval reports mass
    on a side the posterior has none on. Scanning every window of the required
    width is exact over a finite sample, which is what an HPDI over draws is.
    """
    total = ordered.size
    offset = int(math.floor(level * total))
    widths = ordered[offset:] - ordered[: total - offset]
    start = int(np.argmin(widths))
    return float(ordered[start]), float(ordered[start + offset])


def vendor_shrinkage(
    tau_draws: ArrayLike,
    sigma_draws: ArrayLike,
    training_line_counts: Mapping[str, int],
    hdi_probability: float,
) -> dict[str, VendorShrinkage]:
    """One weight per vendor named in the roster, as a median with an HPDI.

    Every vendor asked about comes back, including one with no training line: a
    `CHECK` reads no other table, so membership is unenforceable at the storage
    boundary and this is the tier that can still name the cause of a drop
    (SC-004, DV-009).

    Returned keyed as it was asked for and in the roster's own order, so the
    caller's iteration order is the one written — which container the triples
    are serialized into stays `write.py`'s concern.
    """
    tau, sigma = _paired_scales(tau_draws, sigma_draws)
    _checked_roster(training_line_counts)
    level = _checked_level(hdi_probability)

    # `n·τ²` rather than `τ²/(τ² + σ²/n)`: the two agree wherever both are
    # defined, and only this form gives `n = 0` a value instead of a division.
    tau_squared = np.square(tau)
    sigma_squared = np.square(sigma)

    published: dict[str, VendorShrinkage] = {}
    for vendor, count in training_line_counts.items():
        own_data = int(count) * tau_squared
        ordered = np.sort(own_data / (own_data + sigma_squared))
        low, high = _hpdi(ordered, level)
        published[vendor] = VendorShrinkage(
            median=_nearest_rank_median(ordered), hpdi_low=low, hpdi_high=high
        )
    return published


# ---------------------------------------------------------------------------
# The vendor-effect interval (SC-005, DV-010)
# ---------------------------------------------------------------------------

#: `math.erf` over an array. NumPy carries no error function and neither entry
#: declares SciPy, so the scalar one is vectorized — the same arrangement
#: `test_conditioning.py` uses for `erfc`.
_ERF = np.vectorize(math.erf, otypes=[float])

_SQRT_TWO = math.sqrt(2.0)

#: Bisection steps used to solve the marginal interval below. The bracket is
#: exact, so each step halves it: sixty steps take a bracket of any width to
#: `2⁻⁶⁰` of it, which is beyond double precision long before the last step and
#: cheaper than carrying a convergence test that could fail to converge.
_INTERVAL_SOLVER_STEPS = 60


def _effect_spread(
    tau: NDArray[np.float64], sigma: NDArray[np.float64], count: int
) -> NDArray[np.float64]:
    """`sd(θⱼ|data) = τσ/√(nτ² + σ²)`, draw by draw.

    Written with `nτ²` inside the radicand so `n = 0` is a value rather than a
    division: a vendor with no training line has learned nothing of its own, and
    its effect then carries the population's whole spread τ exactly — which is
    the boundary at which this quantity and the weight part company most
    visibly, the weight there being known exactly at 0.
    """
    return tau * sigma / np.sqrt(count * np.square(tau) + np.square(sigma))


def _marginal_interval(spread: NDArray[np.float64], level: float) -> tuple[float, float]:
    """The HPDI of θⱼ, marginal over the posterior of the two scales.

    Conditional on one draw of `(τ, σ)` the vendor effect is normal about the
    population mean with the spread above, so the posterior of θⱼ is a **scale
    mixture of mean-zero normals** — one component per draw. That mixture is
    symmetric and unimodal, so its highest-density interval is the symmetric one
    `[−h, h]` where `h` solves `mean(erf(h / (sᵢ√2))) = level`.

    Marginal rather than evaluated at the median scales, which would be the
    cheaper thing to write: collapsing the posterior of `(τ, σ)` to a point in
    order to report an interval is the move Principle II exists to refuse, and
    it understates the interval by exactly the fit's uncertainty about its own
    scales — largest at the sparse vendor, which is the vendor SC-005 is about.

    Solved by bisection on a bracket that is exact rather than guessed: coverage
    is zero at `h = 0` and at `h = z·max(s)` every component covers at least
    `level`, so the root is enclosed from the first step and no starting guess
    can miss it.
    """
    widest = float(np.max(spread))
    if widest <= 0.0:
        # Every draw put the vendor exactly on the population mean — τ of zero
        # throughout. The honest interval is the degenerate one; anything wider
        # would report uncertainty the fit does not have.
        return 0.0, 0.0

    # `inf` where the spread is zero: that component is a point mass at the
    # centre and lies inside every interval of positive width, which is what
    # `erf(inf) = 1` gives without a special case in the loop.
    scaled = np.divide(
        1.0,
        spread * _SQRT_TWO,
        out=np.full_like(spread, np.inf),
        where=spread > 0.0,
    )

    low = 0.0
    high = NormalDist().inv_cdf(0.5 + 0.5 * level) * widest
    for _ in range(_INTERVAL_SOLVER_STEPS):
        middle = 0.5 * (low + high)
        if float(np.mean(_ERF(middle * scaled))) < level:
            low = middle
        else:
            high = middle
    half_width = 0.5 * (low + high)
    return -half_width, half_width


def vendor_effect_spread(
    tau_draws: ArrayLike,
    sigma_draws: ArrayLike,
    training_line_counts: Mapping[str, int],
) -> dict[str, NDArray[np.float64]]:
    """`sd(θⱼ|data)` per posterior draw, one sequence per vendor in the roster.

    The draw-level quantity rather than a summary of it, because the relation
    DV-010 rests on holds **draw by draw** — every draw of `(τ, σ)` gives a
    vendor with fewer training lines the larger spread — and a comparison
    between two summaries is a weaker statement that two different summaries
    could disagree about.

    Takes the same two sequences and the same roster as `vendor_shrinkage`, so
    the effect spread and the published weight are two readings of one fitted
    posterior. They are one algebraic step apart: `sd² = ρ·σ²/n`.
    """
    tau, sigma = _paired_scales(tau_draws, sigma_draws)
    _checked_roster(training_line_counts)
    return {
        vendor: _effect_spread(tau, sigma, int(count))
        for vendor, count in training_line_counts.items()
    }


def vendor_effect_interval(
    tau_draws: ArrayLike,
    sigma_draws: ArrayLike,
    training_line_counts: Mapping[str, int],
    hdi_probability: float,
) -> dict[str, VendorEffect]:
    """One vendor-effect interval per vendor, at one stated credible level.

    The quantity SC-005 and DV-010 compare between the vendor with the fewest
    training lines and the vendor with the most. The level is an argument for
    the reason SC-005 gives directly: "wider" is undefined between intervals of
    different mass, so a comparison against an interval carrying another mass is
    not a comparison at all — and A-027 records that no requirement fixes a
    level, so there is no default to fall back on.

    Not a stored quantity — `data-model.md` says so — and therefore computed
    from the caller's own fitted draws each time it is wanted, never read back
    from a column that would have to be trusted.
    """
    tau, sigma = _paired_scales(tau_draws, sigma_draws)
    _checked_roster(training_line_counts)
    level = _checked_level(hdi_probability)

    published: dict[str, VendorEffect] = {}
    for vendor, count in training_line_counts.items():
        spread = _effect_spread(tau, sigma, int(count))
        low, high = _marginal_interval(spread, level)
        published[vendor] = VendorEffect(
            spread_median=_nearest_rank_median(np.sort(spread)),
            hpdi_low=low,
            hpdi_high=high,
        )
    return published
