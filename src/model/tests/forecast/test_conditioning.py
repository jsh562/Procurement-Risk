"""T040 — DV-005's three surviving relations, over the rows `line_posterior` holds.

An open line's stored draws are **conditional remaining durations** and not a
re-based total. That claim is demonstrated by three relations rather than by one
comparison, and the ones this file asserts are the ones a correct implementation
passes:

1. every stored draw is strictly positive — HINT-004's re-based alternative puts
   a point mass of size `F(elapsed)` at exactly zero and satisfies every
   delivered constraint, so this is the exact discriminator between the two;
2. the conditional law equals the truncated law, `count(draws > k)/draw_count`
   agreeing with `S(e+k)/S(e)` over the fit's own posterior;
3. past the P99 of the fitted duration the median stored draw **rises** with
   elapsed time, which the re-based alternative moves the other way.

**Two earlier forms of this rule were struck and are not reinstated here.** The
between-decile comparison — a decile of ~24 open lines is 2.4 lines, and the two
deciles are different lines confounded by vendor and category — and the
`survival[1]` floor "derived from the fitted one-day hazard", which bounds a
quantity by a derivation of itself. Both were removed from SC-027 at the Analyze
gate after being measured.

**Where the parent law comes from.** Relation 2 needs `S`, and no column stores
it, so the run's posterior is reconstructed from the provenance the run row
records — its seed entropy, its chain count, its anchor — over the same rows.
`fit.py`'s own aggregation is imported rather than re-derived: the quantity under
assertion is the *conditioning*, and a second opinion about the Fenton–Wilkinson
step would fail this file for a reason DV-005 is not about.
"""

from __future__ import annotations

import io
import math
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from statistics import NormalDist

import numpy as np
import pytest
from numpy.typing import NDArray
from sqlalchemy import Engine, text

from forecast.conftest import EmittedRun, discard_run
from model.forecast.censoring import censoring_indicator, elapsed_days
from model.forecast.config import CHAINS, DRAWS_PER_CHAIN
from model.forecast.design import category_index, vendor_index
from model.forecast.fit import (
    _flattened,
    _leg_positions,
    _posterior_dataset,
    _rework_loops,
    _total_duration_lognormal,
    run_fit,
)
from model.forecast.model import (
    CATEGORY_DIM,
    VENDOR_DIM,
    VENDOR_OFFSET,
    build_model,
    training_frame,
)
from model.forecast.read import read_lines_and_events
from model.forecast.sample import sample_posterior
from model.forecast.serialize import input_data_hash
from model.forecast.split import assign_split

#: Module-level SQL, never assembled from values (Ruff S608).
STORED_DRAWS_SQL = text(
    "SELECT po_line_id, draws, survival FROM line_posterior WHERE run_id = :run_id"
)
RUN_PROVENANCE_SQL = text(
    """
    SELECT seed_entropy, chain_count, draw_count, tuning_count, as_of_date
    FROM forecast_run WHERE run_id = :run_id
    """
)

#: The days the truncated-law identity is checked at. A published grid rather
#: than every `k`, because the comparison costs one normal tail per posterior
#: draw per day; spanning the first week, the first quarter and the far end, so a
#: grid that is right near the anchor and wrong in the tail is caught.
IDENTITY_DAYS = (1, 2, 3, 5, 7, 14, 30, 45, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330, 365)

#: How many binomial standard errors the identity is allowed to miss by. The
#: stored grid counts one independent uniform per posterior draw, so its
#: deviation from `S(e+k)/S(e)` is a binomial proportion whose standard error is
#: at most `1/(2·√draw_count)`. Eight of them is a family-wise bound over the few
#: hundred comparisons below, published here before any deviation was measured;
#: the re-based alternative misses by `1 − S(e)`, which is two orders larger.
IDENTITY_SIGMA_MULTIPLE = 8.0

#: The percentile relation 3 is stated past, and the far anchor it is measured
#: at. The far date is chosen so every line still open there has been open for
#: longer than the fitted duration's P99 — asserted below rather than assumed.
FITTED_PERCENTILE = 0.99
FAR_ANCHOR = date(2027, 10, 1)

#: The shape the two anchor-comparison runs share, identical between them so the
#: anchor is the only input that moved.
#:
#: **The committed shape rather than a tiny one, and US4 is why.** These runs go
#: through `run_fit`, which now refuses below the four-chain minimum before
#: sampling (FR-035) and refuses again after sampling on any breached blocking
#: diagnostic (FR-017) — and a fifty-draw fit breaches R-hat and both ESS bars on
#: every monitored parameter. A completed run is what this fixture needs, so the
#: only shape available to it is one that converges. The values are read from
#: `config.py` rather than written here, so they follow the published shape.
ANCHOR_RUN_CHAINS = CHAINS
ANCHOR_RUN_DRAWS = DRAWS_PER_CHAIN
ANCHOR_RUN_SEED = 20260728

_ERFC = np.vectorize(math.erfc, otypes=[float])
_SQRT_TWO = math.sqrt(2.0)


# `eq=False` because two fields are arrays and a generated `__eq__` would compare
# elementwise, yielding an array where a bool is expected.
@dataclass(frozen=True, slots=True, eq=False)
class LineLaw:
    """One open line's elapsed time and its fitted total-duration parameters.

    `mu` and `sigma` carry one entry per posterior draw, because AD-002 conditions
    per draw: each stored draw was taken against its own `(μ, σ)`, and averaging
    the parameters before conditioning would be a different quantity.
    """

    elapsed: int
    mu: NDArray[np.float64]
    sigma: NDArray[np.float64]

    def survives(self, days: NDArray[np.float64]) -> NDArray[np.float64]:
        """`S(t) = ½·erfc(z/√2)` for each day, one row per day.

        Written here rather than imported from `posterior.py`, because
        `posterior.py` is what produced the draws under assertion — the survivor
        function has to come from somewhere else for the comparison to be a
        comparison.
        """
        z = (np.log(days)[:, None] - self.mu[None, :]) / (self.sigma[None, :] * _SQRT_TWO)
        return 0.5 * _ERFC(z)

    def conditional_survival(self, days: NDArray[np.float64]) -> NDArray[np.float64]:
        """`S(e+k)/S(e)` averaged over the posterior, one value per day."""
        elapsed = float(self.elapsed)
        at_anchor = 0.5 * _ERFC((math.log(elapsed) - self.mu) / (self.sigma * _SQRT_TWO))
        return np.mean(self.survives(days + elapsed) / at_anchor[None, :], axis=1)

    def percentile(self, probability: float) -> float:
        """The fitted total duration's quantile, summarised across the posterior."""
        return float(np.median(np.exp(self.mu + self.sigma * NormalDist().inv_cdf(probability))))


@pytest.fixture(scope="module")
def fitted_laws(engine: Engine, emitted_run: EmittedRun) -> dict[uuid.UUID, LineLaw]:
    """The shared run's own parent law per open line, reconstructed from its row.

    Every input is taken from the run's recorded provenance rather than from a
    constant here: the seed entropy, the chain count and the anchor come off
    `forecast_run`, and the rows come from the same tables the fit read. The
    sampler is re-run at that seed, so the posterior is the one the stored draws
    were conditioned against rather than another sample of the same posterior —
    which is what makes the identity below a tolerance on the uniforms alone.
    """
    with engine.connect() as connection:
        provenance = (
            connection.execute(RUN_PROVENANCE_SQL, {"run_id": emitted_run.run_id}).mappings().one()
        )
        procurement_input = read_lines_and_events(connection)

    as_of_date = provenance["as_of_date"]
    chains = int(provenance["chain_count"])
    row_hash = input_data_hash(procurement_input)
    split = assign_split(procurement_input.lines, as_of_date, row_hash)
    vendors = tuple(sorted({line.vendor_id for line in procurement_input.lines}))
    categories = tuple(sorted({line.material_category for line in procurement_input.lines}))
    frame = training_frame(procurement_input.lines, split, vendors, categories, as_of_date)

    streams = np.random.SeedSequence(int(provenance["seed_entropy"])).spawn(2)
    chain_seeds = [
        int(child.generate_state(1, dtype=np.uint32)[0]) for child in streams[0].spawn(chains)
    ]
    posterior = _posterior_dataset(
        sample_posterior(
            build_model(frame),
            random_seed=chain_seeds,
            chains=chains,
            draws=int(provenance["draw_count"]) // chains,
            tune=int(provenance["tuning_count"]),
            cores=1,
        )
    )
    mu_sojourn = _flattened(posterior, "mu_sojourn", "transition")
    sigma_sojourn = _flattened(posterior, "sigma_sojourn", "transition")
    vendor_offsets = _flattened(posterior, VENDOR_OFFSET, VENDOR_DIM)
    category_offsets = _flattened(posterior, "category_offset", CATEGORY_DIM)
    vendor_at, category_at = vendor_index(vendors), category_index(categories)

    laws: dict[uuid.UUID, LineLaw] = {}
    for line in procurement_input.lines:
        if not censoring_indicator(line, as_of_date):
            continue
        group = (
            vendor_offsets[:, vendor_at[line.vendor_id]]
            + (category_offsets[:, category_at[line.material_category]])
        )
        mu, sigma = _total_duration_lognormal(
            mu_sojourn,
            sigma_sojourn,
            group,
            _leg_positions(_rework_loops(line, as_of_date)),
        )
        laws[line.po_line_id] = LineLaw(elapsed=elapsed_days(line, as_of_date), mu=mu, sigma=sigma)
    return laws


@pytest.fixture(scope="module")
def anchored_medians(
    engine: Engine, emitted_run: EmittedRun, tmp_path_factory
) -> Iterator[tuple[dict[uuid.UUID, float], dict[uuid.UUID, float]]]:
    """Per-line stored medians from two runs differing only in the as-of date.

    Both are emitted at the same shape and the same seed, so the one input that
    moved is the anchor — which is the comparison relation 3 needs and the one a
    single run cannot make, since a line has exactly one elapsed time per run.
    Both runs are discarded afterwards: this tier leaves `forecast_run` empty.
    """
    root = tmp_path_factory.mktemp("anchor-comparison")
    written: list[uuid.UUID] = []
    try:
        for anchor in (emitted_run.as_of_date, FAR_ANCHOR):
            written.append(
                run_fit(
                    engine,
                    as_of_date=anchor,
                    seed_entropy=ANCHOR_RUN_SEED,
                    chains=ANCHOR_RUN_CHAINS,
                    draws=ANCHOR_RUN_DRAWS,
                    tune=ANCHOR_RUN_DRAWS,
                    cores=1,
                    report_root=root,
                    log=io.StringIO(),
                )
            )
        with engine.connect() as connection:
            near, far = (
                {
                    row["po_line_id"]: float(np.median(np.asarray(row["draws"], dtype=float)))
                    for row in connection.execute(STORED_DRAWS_SQL, {"run_id": run_id}).mappings()
                }
                for run_id in written
            )
        yield near, far
    finally:
        for run_id in written:
            discard_run(engine, run_id)


def _stored(engine: Engine, run_id: uuid.UUID) -> list:
    """The run's artifact rows, as mappings."""
    with engine.connect() as connection:
        return list(connection.execute(STORED_DRAWS_SQL, {"run_id": run_id}).mappings().all())


def test_every_stored_draw_is_strictly_positive(engine: Engine, emitted_run: EmittedRun) -> None:
    """Relation 1, and the exact discriminator against the re-based alternative.

    Re-basing a total draw — subtracting elapsed days and clipping at zero — puts
    a point mass of size `F(elapsed)` at exactly zero, which on a line open for
    four months is a large share of the draws. It passes
    `ck_line_posterior__draws_non_negative` and every other delivered constraint,
    so a strict `> 0` over the stored column is the whole of what separates the
    two implementations at the storage boundary.
    """
    rows = _stored(engine, emitted_run.run_id)

    assert rows, "the emitted run stored no draws"
    for row in rows:
        draws = np.asarray(row["draws"], dtype=float)

        assert np.all(draws > 0.0), (
            f"line {row['po_line_id']} stored {int(np.count_nonzero(draws <= 0.0))} draw(s) at "
            f"or below zero; a conditional remaining duration is strictly positive"
        )


def test_the_stored_grid_is_the_truncated_law_of_the_fits_own_posterior(
    engine: Engine, emitted_run: EmittedRun, fitted_laws: dict[uuid.UUID, LineLaw]
) -> None:
    """Relation 2: `count(draws > k)/draw_count` agrees with `S(e+k)/S(e)`.

    The stored grid is compared against the parent law reconstructed from the
    run's own provenance, so the only randomness left is the per-draw uniform the
    inverse-CDF consumed — which is why a binomial standard error is the right
    scale for the tolerance and why the published multiple is a family-wise bound
    rather than a number chosen after a deviation was seen.
    """
    days = np.asarray(IDENTITY_DAYS, dtype=float)
    rows = _stored(engine, emitted_run.run_id)
    tolerance = None
    for row in rows:
        survival = np.asarray(row["survival"], dtype=float)
        law = fitted_laws[row["po_line_id"]]
        if tolerance is None:
            draw_count = len(row["draws"])
            tolerance = IDENTITY_SIGMA_MULTIPLE / (2.0 * math.sqrt(draw_count))
        stored = survival[days.astype(int) - 1]
        expected = law.conditional_survival(days)

        assert np.max(np.abs(stored - expected)) <= tolerance, (
            f"line {row['po_line_id']} (elapsed {law.elapsed} days) misses the truncated law "
            f"by {np.max(np.abs(stored - expected)):.4f}, past the published "
            f"{tolerance:.4f}; the largest gap is at day "
            f"{IDENTITY_DAYS[int(np.argmax(np.abs(stored - expected)))]}"
        )


def test_the_two_anchors_straddle_the_fitted_percentile_on_every_line(
    emitted_run: EmittedRun,
    fitted_laws: dict[uuid.UUID, LineLaw],
    anchored_medians: tuple[dict[uuid.UUID, float], dict[uuid.UUID, float]],
) -> None:
    """Relation 3's precondition, measured rather than assumed.

    The relation is stated *past the P99 of the fitted duration*, so the two
    anchors have to straddle it: at the near anchor every line's elapsed time is
    below its own fitted P99 and at the far one every line's is above it. Without
    this the comparison below would be a claim about the ordinary regime, where
    the median remaining duration is not required to rise at all.
    """
    near, far = anchored_medians
    shared = set(near) & set(far) & set(fitted_laws)
    anchor_gap = (FAR_ANCHOR - emitted_run.as_of_date).days

    assert shared, "the two anchors share no line, so nothing can be compared per line"
    assert anchor_gap > 0
    for po_line_id in sorted(shared, key=str):
        law = fitted_laws[po_line_id]
        percentile = law.percentile(FITTED_PERCENTILE)

        assert law.elapsed < percentile, (
            f"line {po_line_id} is already past its fitted P99 at the near anchor "
            f"({law.elapsed} days against {percentile:.1f}), so the pair does not straddle it"
        )
        assert law.elapsed + anchor_gap > percentile, (
            f"line {po_line_id} is still short of its fitted P99 at the far anchor "
            f"({law.elapsed + anchor_gap} days against {percentile:.1f}), so the comparison "
            f"below would not be the one DV-005 states"
        )


def test_past_the_fitted_percentile_the_median_draw_rises_with_elapsed_time(
    fitted_laws: dict[uuid.UUID, LineLaw],
    anchored_medians: tuple[dict[uuid.UUID, float], dict[uuid.UUID, float]],
) -> None:
    """Relation 3, per line, over two runs whose only difference is the anchor.

    Compared **within** each line rather than across lines, which is what the
    struck decile comparison could not do: two deciles of a twenty-four-line
    population are different lines confounded by vendor and category, while one
    line at two anchors is the same vendor, the same category and the same walk.

    Both runs fit their own posterior, and the far anchor's longer censored legs
    push the fitted durations up as well — the two effects share a direction and
    are not separated here. What the comparison discriminates is the sign: a
    re-based total draw moves the median *down* toward zero as elapsed time
    grows, which is the implementation HINT-004 warns about.
    """
    near, far = anchored_medians
    shared = sorted(set(near) & set(far), key=str)

    assert shared, "the two anchors share no line"
    fell = [po_line_id for po_line_id in shared if far[po_line_id] <= near[po_line_id]]

    assert not fell, (
        f"{len(fell)} of {len(shared)} line(s) forecast a median remaining duration at the far "
        f"anchor no larger than at the near one — first {fell[0]}: "
        f"{near[fell[0]]:.2f} then {far[fell[0]]:.2f} days"
    )
    assert set(shared) <= set(fitted_laws)
