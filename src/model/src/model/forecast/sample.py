"""Seeded NUTS over AD-001's graph, at the shape `config.py` publishes.

Four chains, 1,000 post-warmup draws each and 1,000 tuning draws — the plan's
§ Sampling shape, read from `config.py` rather than restated, so the declared
`draw_count` and the shape actually asked for cannot drift apart. The shape is
overridable per call because the unit tier must reach this function at two
chains and fifty draws; FR-035's four-chain refusal is a *precondition in the
job*, asserted over emitted runs by DV-035, and putting it here would make the
only test that exercises the sampler cost a full fit.

The seed is the caller's, deliberately. `SPLIT_SEED` is a committed constant
because a per-run split seed lets a re-fit reshuffle until a vendor lands well
(AD-005); the sampling seed is the opposite case — it is recorded per run in
`forecast_run.seed_entropy`, and a constant here would be a fourth home for it.
"""

from __future__ import annotations

from collections.abc import Sequence

import arviz as az
import pymc as pm

from model.forecast.config import CHAINS, DRAWS_PER_CHAIN, TUNING_DRAWS_PER_CHAIN

__all__ = ["SampleError", "sample_posterior"]


class SampleError(ValueError):
    """Raised when a sampling shape or a model cannot produce a posterior.

    A `ValueError`: every case is an argument the caller passed. Named as its
    own type so a refusal here is distinguishable from a PyMC or PyTensor
    failure inside the sampler.
    """


def _positive(name: str, value: int, floor: int) -> int:
    """One sampling count, proved a whole number at or above its floor.

    `bool` is excluded explicitly because it is an `int` subclass, and
    `chains=True` would otherwise run one chain and look deliberate.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise SampleError(
            f"{name} is a whole number of {'chains' if name == 'chains' else 'draws'}, "
            f"found {type(value).__name__}; a fractional count has no meaning to the "
            f"sampler and would be coerced silently"
        )
    if value < floor:
        raise SampleError(
            f"{name} must be at least {floor}, found {value}; a run below that produces an "
            f"`InferenceData` the diagnostics gate can still summarise, which is how an "
            f"empty posterior reaches a stored artifact"
        )
    return value


def sample_posterior(
    model: pm.Model,
    *,
    random_seed: int | Sequence[int],
    chains: int = CHAINS,
    draws: int = DRAWS_PER_CHAIN,
    tune: int = TUNING_DRAWS_PER_CHAIN,
    cores: int | None = None,
    progressbar: bool = False,
) -> az.InferenceData:
    """Sample `model` and return the posterior with its sampler statistics.

    An `InferenceData` rather than a trace, because the diagnostics gate reads
    `sample_stats` — `diverging` and `energy` — beside the draws, and a
    container that separated them would let a run be summarised without the two
    run-scope metrics FR-017 refuses on (`research.md` § Posterior storage).

    `random_seed` is passed through unchanged: PyMC derives one stream per chain
    from it, so the same seed at the same shape and library versions reproduces
    the same draws. ADR-0009 already rules out bitwise equality as the *gate*,
    which is why FR-022 compares through a published tolerance instead — the
    seed is recorded so a re-run is a re-run, not so a float is a float.

    Two defaults are load-bearing rather than cosmetic. `progressbar` is off
    because PyMC's rich backend imports `matplotlib`, which this entry does not
    declare and E007 may not add — asking for one raises `ModuleNotFoundError`
    from inside the sampler. And `cores` defaults to one worker per chain, which
    on Windows spawns processes: a caller invoking this from an unguarded script
    body will deadlock, so a bare script passes `cores=1` or guards its entry.
    """
    if not isinstance(model, pm.Model):
        raise SampleError(
            f"a PyMC model is required, found {type(model).__name__}; `build_model` "
            f"returns one, and sampling anything else would report a shape error from "
            f"inside the sampler rather than name the caller's mistake"
        )
    if random_seed is None:
        raise SampleError(
            "no sampling seed was supplied. The seed is recorded in "
            "`forecast_run.seed_entropy` and is what makes a re-fit a re-fit; defaulting "
            "it here would put an unrecorded constant behind every published run"
        )
    _positive("chains", chains, 1)
    _positive("draws", draws, 1)
    _positive("tune", tune, 1)

    with model:
        return pm.sample(
            draws=draws,
            tune=tune,
            chains=chains,
            cores=cores if cores is not None else chains,
            random_seed=random_seed,
            progressbar=progressbar,
            return_inferencedata=True,
        )
