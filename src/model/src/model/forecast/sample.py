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
from typing import TYPE_CHECKING

import pymc as pm

from model.forecast.config import CHAINS, DRAWS_PER_CHAIN, TUNING_DRAWS_PER_CHAIN

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation only
    # `az.InferenceData` no longer exists. ArviZ 1.x moved the role to
    # `xarray.DataTree` and left the old attribute as a shim that raises a
    # `MigrationWarning` on *access*, so annotating the return as
    # `az.InferenceData` made `typing.get_type_hints(sample_posterior)` fail under
    # `-W error` — deferred annotations were the only reason nothing broke.
    #
    # Guarded rather than imported at runtime so naming the type implies no
    # dependency decision: xarray reaches this entry transitively through ArviZ
    # and is not declared, and this module wants the name and never a value. The
    # cost is the ordinary one of a guarded import — a caller resolving the
    # annotation at runtime must supply this module's `TYPE_CHECKING` names — and
    # it is preferred to a runtime import because the alternative would have this
    # module assert a dependency the entry's manifest does not.
    import xarray as xr

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
) -> xr.DataTree:
    """Sample `model` and return the posterior with its sampler statistics.

    A grouped container rather than a trace, because the diagnostics gate reads
    `sample_stats` — `diverging` and `energy` — beside the draws, and a
    container that separated them would let a run be summarised without the two
    run-scope metrics FR-017 refuses on (`research.md` § Posterior storage). The
    container is an `xarray.DataTree`: ArviZ 1.x retired `InferenceData` and moved
    the role there, which is why the annotation names xarray's type.

    `random_seed` is passed through unchanged: PyMC derives one stream per chain
    from it, so the same seed at the same shape drives the same *sequence of
    decisions*. It does **not** follow that a whole recorded *run* reproduces the
    same bits, and that is measured rather than argued: on Linux a re-fit of one
    recorded run at its own seed and shape, under a library pin equal on all six
    keys, moved every one of 68 lines' stored digests, while the realized median
    drift was 0.12 days against a 5.0-day tolerance. On Windows none moved.

    **The mechanism is unestablished** (G-21), and three candidates are already
    ruled out on that same Linux image against the real database, so the next
    reader need not re-measure them: sampling twice from one built graph at one
    seed returns all ten posterior variables bitwise identical; rebuilding the
    graph and resampling returns the same; and a float64 array — 4005 values
    including subnormals, `nextafter(1, 2)` and `1/3` — survives the Postgres
    round-trip bitwise, worst delta `0.0`. This function at a fixed seed is
    therefore not the source, on the evidence available. ADR-0009 rules out
    bitwise equality as the *gate* independently of any of that, which is why
    FR-022 compares through a published tolerance — the seed is recorded so a
    re-run is a re-run, not so a float is a float.

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
