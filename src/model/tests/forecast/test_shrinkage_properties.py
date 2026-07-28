"""T030 (RED) — the per-vendor shrinkage weight, at the mandatory property tier.

`plan.md` § Mandated properties gives `shrinkage.py` two **Invariant**
relations. First: `ρ = τ²/(τ² + σ²/n)` is monotone increasing in `n` and lies
in `[0,1]`, and a vendor with no training line yields a weight rather than an
omission. Second: the published value is a **triple** ordered
`hpdi_low ≤ median ≤ hpdi_high`, all inside `[0,1]`. Domain: `n = 0`, `n = 1`,
the 35-line vendor, `τ → 0` and `τ → ∞`. The module under test does not exist
yet — this file is the RED half of T030/T031 and must fail at collection.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from model.forecast.shrinkage import vendor_shrinkage
from numpy.typing import NDArray

# ---------------------------------------------------------------------------
# The interface this file pins
# ---------------------------------------------------------------------------
#
# `vendor_shrinkage(tau_draws, sigma_draws, training_line_counts,
# hdi_probability)` returns one entry per vendor named in
# `training_line_counts`, each publishing **`median`**, **`hpdi_low`** and
# **`hpdi_high`** — the three names `fn_vendor_shrinkage_wellformed` validates in
# migration `0300`, which accepts `{median, hpdi_low, hpdi_high}` objects and
# not bare numbers. Read here by name rather than by position, so which
# container `shrinkage.py` puts them in stays `write.py`'s serialization concern.
#
# `hdi_probability` is passed explicitly everywhere below. SC-005 requires the
# credible level to be *stated* — "wider" is undefined between intervals of
# different mass — and A-027 records that no requirement fixes one, so this file
# names the level at every call rather than resting on a default.
#
# ---------------------------------------------------------------------------
# What this file does not assert, and why
# ---------------------------------------------------------------------------
#
# `plan.md` row 173 ends "and the interval **widens** as nⱼ falls", with the
# no-training-line vendor named as the case where it "must be widest rather than
# absent". Of that clause, only the part asserted below is true of the published
# triple, and the arithmetic says why. Writing `r = τ²/σ²` gives
# `ρ = n·r/(n·r + 1)`, so `ρ` is a logistic in `log n`: the interval it induces
# is **widest where the median weight is nearest 0.5** and collapses at both
# ends. At `n = 0` every draw of `ρ` is exactly 0, so the honest interval is
# degenerate — the *narrowest* in the set, not the widest — and at large `n`
# every draw approaches 1 and it collapses again. The width therefore peaks near
# `n = σ²/τ²`; at E005's published `0.22`-at-`n = 5` that peak sits near `n = 18`,
# where the 5-line vendor's interval is **narrower** than the 35-line vendor's.
#
# So this file asserts the widening in the regime that carries it, with the
# basis condition published beside it (`test_the_interval_widens_as_the_training
# _count_falls`), and asserts presence plus a degenerate interval at `n = 0`.
# The monotone widening the clause reaches for is a true statement about the
# **vendor-effect** interval — `sd(θⱼ | data) = τσ/√(nτ² + σ²)`, decreasing in
# `n` without a turning point — which is DV-010 / SC-005 / NC-11 and belongs to
# T043 in this same file, not to the weight's own HPDI.

#: The three published names, in the order `0300`'s helper lists them.
FIELDS = ("median", "hpdi_low", "hpdi_high")

#: The credible level this file compares intervals at. ArviZ's own default, so a
#: reference implementation needs no argument translation; the value matters
#: less than that every comparison below uses the same one (SC-005).
HDI_PROBABILITY = 0.94

#: The two vendors the plan's domain column names, and the roster form
#: `fn_vendor_shrinkage_wellformed` requires of every key: `^VND-[0-9]{3}$`.
SPARSE_VENDOR = "VND-001"
DENSE_VENDOR = "VND-002"
ABSENT_VENDOR = "VND-003"

#: The realized counts: five lines at the smallest vendor and thirty-five at the
#: largest, from `spec.md` § Edge Cases and `research.md`.
SPARSE_COUNT = 5
DENSE_COUNT = 35


# ---------------------------------------------------------------------------
# A stand-in posterior
# ---------------------------------------------------------------------------


def posterior(
    tau_median: float,
    tau_spread: float,
    sigma_median: float,
    sigma_spread: float,
    *,
    count: int = 2000,
    seed: int = 20260727,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Draws of `(τ, σ)` around stated medians, lognormal on each.

    A stand-in rather than a fit: every relation below is a statement about the
    plug-in, and running a sampler to obtain two positive sequences would make
    this tier depend on the thing it is meant to check independently. Lognormal
    because both parameters are scales, and seeded because the suite is
    derandomized — a determinism-adjacent property that redrew its own inputs
    each run would report defects nobody can reproduce.
    """
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal((2, count))
    return (
        np.exp(math.log(tau_median) + tau_spread * noise[0]),
        np.exp(math.log(sigma_median) + sigma_spread * noise[1]),
    )


#: One posterior reused across the Hypothesis-driven tests, short enough that
#: 200 examples over a twelve-vendor roster stay cheap.
TAU_DRAWS, SIGMA_DRAWS = posterior(0.30, 0.25, 0.50, 0.10, count=400)


def plug_in(
    tau: NDArray[np.float64], sigma: NDArray[np.float64], count: int
) -> NDArray[np.float64]:
    """`ρ = τ²/(τ² + σ²/n)` draw by draw — the quantity, without the summary.

    Written in the `n·τ²/(n·τ² + σ²)` form so `n = 0` is a value rather than a
    division by zero: with no training line the weight on the vendor's own data
    is exactly 0, and that is arithmetic rather than a special case.
    """
    return count * tau**2 / (count * tau**2 + sigma**2)


# ---------------------------------------------------------------------------
# Calling conventions
# ---------------------------------------------------------------------------


def triple_of(value: Any) -> tuple[float, float, float]:
    """The three published numbers, by the names migration `0300` validates."""
    if isinstance(value, Mapping):
        assert set(value) == set(FIELDS), (
            f"`fn_vendor_shrinkage_wellformed` admits exactly {sorted(FIELDS)} per "
            f"vendor and rejects any other key set; got {sorted(value)}"
        )
        return (float(value["median"]), float(value["hpdi_low"]), float(value["hpdi_high"]))
    for name in FIELDS:
        assert hasattr(value, name), (
            f"the published weight must carry {name!r}: `0300` validates "
            f"`{{median, hpdi_low, hpdi_high}}` objects, not bare numbers"
        )
    return (float(value.median), float(value.hpdi_low), float(value.hpdi_high))


def weights(
    tau: Any,
    sigma: Any,
    counts: Mapping[str, int],
    *,
    hdi_probability: float = HDI_PROBABILITY,
) -> dict[str, tuple[float, float, float]]:
    """Every vendor's triple, keyed as it was asked for."""
    published = vendor_shrinkage(
        tau_draws=tau,
        sigma_draws=sigma,
        training_line_counts=counts,
        hdi_probability=hdi_probability,
    )
    assert set(published) == set(counts), (
        f"every vendor asked about must come back with a weight (SC-004, DV-009); "
        f"asked {sorted(counts)}, got {sorted(published)}"
    )
    return {vendor: triple_of(value) for vendor, value in published.items()}


def width(triple: tuple[float, float, float]) -> float:
    _, low, high = triple
    return high - low


def sweep(counts: tuple[int, ...], tau: Any, sigma: Any) -> list[tuple[float, float, float]]:
    """One triple per training count, in the order given."""
    roster = {f"VND-{index + 1:03d}": count for index, count in enumerate(counts)}
    published = weights(tau, sigma, roster)
    return [published[vendor] for vendor in roster]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: A roster in the form the shape helper requires. Up to twelve members, which
#: is the committed dataset's vendor count, and counts reaching zero because a
#: vendor with no training line is a member of the domain rather than an
#: exception to it.
vendor_ids = st.integers(min_value=1, max_value=999).map(lambda index: f"VND-{index:03d}")
rosters = st.dictionaries(
    keys=vendor_ids,
    values=st.integers(min_value=0, max_value=60),
    min_size=1,
    max_size=12,
)


# ---------------------------------------------------------------------------
# Invariant: the weight lies in [0,1] and is monotone in the training count
# ---------------------------------------------------------------------------


@given(roster=rosters)
def test_every_published_number_lies_inside_the_unit_interval(roster: dict[str, int]) -> None:
    """`ρ` is a variance ratio, so all three numbers are probabilities.

    `fn_vendor_shrinkage_wellformed` enforces this on the stored value, which
    means any number in `[0,1]` satisfies it — the plan's own reason this module
    is property-tested rather than left to the constraint.
    """
    for triple in weights(TAU_DRAWS, SIGMA_DRAWS, roster).values():
        for value in triple:
            assert 0.0 <= value <= 1.0


@given(roster=rosters)
def test_the_published_value_is_a_triple_ordered_low_median_high(
    roster: dict[str, int],
) -> None:
    """`hpdi_low ≤ median ≤ hpdi_high`, which is M-2's whole content.

    An earlier revision stored a bare number. `ρ` is a plug-in of two *fitted*
    parameters, so it has a posterior of its own, and a point estimate of an
    uncertain quantity — reported at exactly the sparse-vendor end where the
    uncertainty is largest — is the shape Principle II exists to refuse.
    """
    for vendor, (median, low, high) in weights(TAU_DRAWS, SIGMA_DRAWS, roster).items():
        assert low <= median <= high, f"{vendor} published an unordered triple"


@given(roster=rosters)
def test_every_vendor_asked_about_comes_back_with_a_weight(roster: dict[str, int]) -> None:
    """SC-004 and DV-009's membership half, at the tier that can still name a cause.

    The constraint can enforce shape and not membership — a `CHECK` reads no
    other table — so a vendor silently dropped from the object is **G-9**, and
    the only mechanisms against it are this and T042's roster test.
    """
    published = weights(TAU_DRAWS, SIGMA_DRAWS, roster)

    assert set(published) == set(roster)


@pytest.mark.parametrize(
    ("tau_median", "sigma_median"), [(0.30, 0.50), (0.11, 0.50), (0.45, 0.40)]
)
def test_the_median_weight_is_monotone_increasing_in_the_training_count(
    tau_median: float, sigma_median: float
) -> None:
    """The first mandated invariant, swept across the domain the plan names.

    Every draw of `ρ` rises with `n`, so every order statistic of the posterior
    does — the median included, whichever percentile convention produces it.
    The sweep runs through `n = 0`, `n = 1` and the 35-line vendor, so the three
    named boundaries are inside one monotone chain rather than each asserted on
    its own against a number chosen here.
    """
    tau, sigma = posterior(tau_median, 0.25, sigma_median, 0.10)
    counts = (0, 1, 2, 3, 4, SPARSE_COUNT, 10, 20, DENSE_COUNT, 60)
    medians = [triple[0] for triple in sweep(counts, tau, sigma)]

    assert all(
        later >= earlier for earlier, later in zip(medians, medians[1:], strict=False)
    ), f"the shrinkage weight fell as the vendor gained training lines: {medians}"
    assert medians[0] == 0.0
    assert medians[-1] > medians[0]


@pytest.mark.parametrize("count", [1, SPARSE_COUNT, DENSE_COUNT])
def test_the_median_weight_is_the_plug_in_of_the_two_fitted_scales(count: int) -> None:
    """The weight is `τ²/(τ² + σ²/n)` and not some other number in `[0,1]`.

    Bracketed between the 49th and 51st percentiles of the plug-in rather than
    compared against one median: `schema_constants.percentile_convention` is
    nearest-rank and NumPy's median interpolates, and a property that could not
    tell those apart from a wrong formula would be asserting the convention
    instead of the arithmetic.
    """
    tau, sigma = posterior(0.30, 0.25, 0.50, 0.10)
    expected = plug_in(tau, sigma, count)
    low, high = np.quantile(expected, [0.49, 0.51])
    median = sweep((count,), tau, sigma)[0][0]

    assert low <= median <= high


def test_a_vendor_with_no_training_line_gets_a_weight_rather_than_an_omission() -> None:
    """`n = 0`, the boundary SC-004 names first: a weight, not a missing key.

    The weight is exactly 0 — none of this vendor's estimate is its own data —
    and Principle III's reason for recording it rather than dropping it is that
    an absent vendor reads as an oversight while a zero reads as a measurement.
    """
    tau, sigma = posterior(0.30, 0.25, 0.50, 0.10)
    roster = {SPARSE_VENDOR: SPARSE_COUNT, DENSE_VENDOR: DENSE_COUNT, ABSENT_VENDOR: 0}
    published = weights(tau, sigma, roster)

    assert ABSENT_VENDOR in published
    median, low, high = published[ABSENT_VENDOR]
    assert low <= median <= high
    assert median == 0.0


def test_a_vendor_with_no_training_line_carries_no_uncertainty_about_its_weight() -> None:
    """`n = 0` makes every draw of `ρ` exactly 0, so the honest interval is degenerate.

    This is the one point where the plan's boundary note — "the interval must be
    widest rather than absent" — parts company with the arithmetic it is a note
    about. `ρ = n·τ²/(n·τ² + σ²)` is 0 for every `(τ, σ)` when `n` is 0, so there
    is nothing left for an interval to express: the *weight* is known exactly
    even though the vendor's *effect* is not. Publishing `[0, 1]` here would
    claim the fit cannot tell how much of this vendor's estimate is its own
    data, when the answer is none of it.

    The claim the note is reaching for is DV-010's, about the vendor-effect
    interval, and it is T043's to assert.
    """
    tau, sigma = posterior(0.30, 0.25, 0.50, 0.10)
    published = weights(tau, sigma, {ABSENT_VENDOR: 0, SPARSE_VENDOR: SPARSE_COUNT})

    assert published[ABSENT_VENDOR] == (0.0, 0.0, 0.0)
    assert width(published[SPARSE_VENDOR]) > 0.0


@pytest.mark.parametrize("count", [1, SPARSE_COUNT, DENSE_COUNT])
def test_a_vanishing_between_vendor_spread_pools_every_vendor_completely(count: int) -> None:
    """`τ → 0`: no vendor differs from the population, so no estimate is its own.

    One of the two limits the domain column names, and the direction that has to
    hold whatever the training count is — otherwise a large vendor would be
    reported as standing on its own data inside a hierarchy that found no
    between-vendor variation at all.
    """
    tau, sigma = posterior(1e-8, 0.25, 0.50, 0.10)
    median, low, high = sweep((count,), tau, sigma)[0]

    assert median == pytest.approx(0.0, abs=1e-9)
    assert high == pytest.approx(0.0, abs=1e-9)
    assert low == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("count", [1, SPARSE_COUNT, DENSE_COUNT])
def test_an_unbounded_between_vendor_spread_leaves_every_vendor_on_its_own_data(
    count: int,
) -> None:
    """`τ → ∞`: no pooling is available, so every estimate is the vendor's own.

    The other named limit. Together with the one above these pin the two ends of
    the ratio, which is what makes the monotone sweep a statement about `ρ`
    rather than about an arbitrary increasing function of `n`.
    """
    tau, sigma = posterior(1e8, 0.25, 0.50, 0.10)
    median, low, high = sweep((count,), tau, sigma)[0]

    assert median == pytest.approx(1.0, abs=1e-9)
    assert low == pytest.approx(1.0, abs=1e-9)
    assert high == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Invariant: the interval, and where it widens
# ---------------------------------------------------------------------------


def test_the_five_line_vendor_is_not_published_as_a_certainty() -> None:
    """The sparse vendor's weight is the one a reader leans on hardest (L-4).

    Its interval must be strictly non-degenerate: the smallest vendor carries
    five lines and leaves roughly four after the split, its estimate is mostly
    prior, and a bare number there is exactly the reading L-4 exists to prevent.
    Asserted strictly rather than against a width threshold, because there is no
    independent expected width and a threshold would be a number chosen here.
    """
    tau, sigma = posterior(0.30, 0.25, 0.50, 0.10)
    median, low, high = sweep((SPARSE_COUNT,), tau, sigma)[0]

    assert low < median < high
    assert low > 0.0
    assert high < 1.0


@pytest.mark.parametrize(("tau_median", "sigma_median"), [(0.30, 0.50), (0.45, 0.40)])
def test_the_interval_widens_as_the_training_count_falls(
    tau_median: float, sigma_median: float
) -> None:
    """The second mandated invariant, with its basis condition published beside it.

    Swept downward through the 35-line and 5-line vendors the plan's domain
    column names, so the comparison between the two extremes is inside the chain
    rather than asserted alone.

    **Basis condition.** `ρ = n·r/(n·r + 1)` with `r = τ²/σ²` is a logistic in
    `log n`, so the induced interval is widest where the median weight is
    nearest 0.5 and narrows on both sides of it: the width peaks near
    `n = σ²/τ²`, and the monotone claim holds for counts above that peak. Both
    parameterisations here put the peak below `n = 5` (`σ/τ` of 1.67 and 0.89),
    which is the regime the committed roster sits in if the fitted between-
    vendor spread is an appreciable fraction of the residual. Where it is not —
    at E005's published `0.22`-at-`n = 5`, whose implied peak is near `n = 18` —
    the 5-line vendor's interval is the *narrower* of the two, and no
    implementation of this formula can make it otherwise.
    """
    tau, sigma = posterior(tau_median, 0.25, sigma_median, 0.10)
    counts = (60, DENSE_COUNT, 20, 10, SPARSE_COUNT)
    widths = [width(triple) for triple in sweep(counts, tau, sigma)]

    assert all(
        later > earlier for earlier, later in zip(widths, widths[1:], strict=False)
    ), (
        f"the interval did not widen as the training count fell: "
        f"{list(zip(counts, widths, strict=True))}"
    )


@pytest.mark.parametrize("count", [1, SPARSE_COUNT, DENSE_COUNT, 60])
def test_the_interval_brackets_the_median_inside_the_unit_interval_at_every_count(
    count: int,
) -> None:
    """Ordering and range together, at the counts the domain column names.

    Separate from the Hypothesis sweep because that one runs against a single
    posterior; this one is the statement a reader of the stored JSONB relies on,
    and it has to hold at the 35-line vendor as squarely as at the 1-line one.
    """
    tau, sigma = posterior(0.30, 0.25, 0.50, 0.10)
    median, low, high = sweep((count,), tau, sigma)[0]

    assert 0.0 <= low <= median <= high <= 1.0


@pytest.mark.parametrize("level", [0.5, 0.8, 0.94])
def test_a_wider_credible_level_gives_a_wider_interval(level: float) -> None:
    """The level is stated because "wider" is undefined between different masses.

    SC-005 says so directly and A-027 records that no requirement fixed one. A
    module that accepted the argument and ignored it would publish intervals
    that cannot be compared across runs, and nothing downstream would notice.
    """
    tau, sigma = posterior(0.30, 0.25, 0.50, 0.10)
    roster = {SPARSE_VENDOR: SPARSE_COUNT}
    narrow = weights(tau, sigma, roster, hdi_probability=level)[SPARSE_VENDOR]
    wide = weights(tau, sigma, roster, hdi_probability=0.99)[SPARSE_VENDOR]

    assert width(wide) > width(narrow)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_a_negative_training_count_is_refused() -> None:
    """A count is a cardinality. Negative `n` makes `ρ` leave `[0,1]` silently.

    At `n = −1` the plug-in is `−τ²/(σ² − τ²)`, which is a finite number outside
    the unit interval for most draws and a division by zero for one of them —
    and `fn_vendor_shrinkage_wellformed` would reject the row long after the
    sampling run that produced it.
    """
    with pytest.raises(ValueError):
        vendor_shrinkage(
            tau_draws=TAU_DRAWS,
            sigma_draws=SIGMA_DRAWS,
            training_line_counts={SPARSE_VENDOR: -1},
            hdi_probability=HDI_PROBABILITY,
        )


def test_an_empty_roster_is_refused() -> None:
    """`vendor_shrinkage` NOT NULL with a shape helper that requires one member.

    An empty object is the one value that satisfies "every vendor asked about
    came back" vacuously, so it is refused where the caller is still named
    rather than at the constraint.
    """
    with pytest.raises(ValueError):
        vendor_shrinkage(
            tau_draws=TAU_DRAWS,
            sigma_draws=SIGMA_DRAWS,
            training_line_counts={},
            hdi_probability=HDI_PROBABILITY,
        )


def test_two_parameter_sequences_of_different_lengths_are_refused() -> None:
    """`ρ` is a plug-in *per draw*, so the two sequences are paired, not pooled.

    NumPy would broadcast a length-1 sequence and raise on any other mismatch,
    which means the one case that passes silently is the one that quietly
    conditions every vendor on a single posterior draw.
    """
    with pytest.raises(ValueError):
        vendor_shrinkage(
            tau_draws=TAU_DRAWS,
            sigma_draws=SIGMA_DRAWS[:-1],
            training_line_counts={SPARSE_VENDOR: SPARSE_COUNT},
            hdi_probability=HDI_PROBABILITY,
        )


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.5, 1.5])
def test_a_credible_level_outside_the_open_unit_interval_is_refused(bad: float) -> None:
    """A level of 0 is a point and a level of 1 is the whole support.

    Neither is an interval a reader can act on, and both would satisfy
    `hpdi_low <= median <= hpdi_high` — which is the shape check, so the
    constraint would accept the row.
    """
    with pytest.raises(ValueError):
        vendor_shrinkage(
            tau_draws=TAU_DRAWS,
            sigma_draws=SIGMA_DRAWS,
            training_line_counts={SPARSE_VENDOR: SPARSE_COUNT},
            hdi_probability=bad,
        )


@pytest.mark.parametrize("scale", [0.0, -0.5])
def test_a_non_positive_residual_scale_is_refused(scale: float) -> None:
    """`σ = 0` makes every weight 1 by division-free accident rather than by fit.

    The same refusal `likelihood.py` makes of its own log-scale, and for the
    same reason: a scale of zero is not a narrow posterior, it is an input that
    never came from a sampler.
    """
    with pytest.raises(ValueError):
        vendor_shrinkage(
            tau_draws=TAU_DRAWS,
            sigma_draws=np.full_like(SIGMA_DRAWS, scale),
            training_line_counts={SPARSE_VENDOR: SPARSE_COUNT},
            hdi_probability=HDI_PROBABILITY,
        )
