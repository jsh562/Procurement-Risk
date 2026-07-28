"""E007's published constants, committed configuration rather than run flags.

Every value is fixed by `spec.md` § Published Constants, the plan's § Sampling
shape, AD-004, AD-005 and AD-006, and every one carries its **row class** in a
comment — blocking diagnostic, blocking precondition, reported, tolerance —
because FR-017 quantifies over the blocking diagnostics only, FR-035 over the
blocking preconditions only, and neither reaches a reported or tolerance row.

Nothing here is a flag. A per-invocation value is the move FR-028 prohibits and
the database cannot detect (G-11); as a constant, changing one is a diff with an
author and a date. The draw count and the horizon are deliberately *absent* as
literals — AD-009 reads both from `schema_constants` over the connection, so
this module offers `read_run_shape()` and no number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from model.procurement.lifecycle import LEGAL_TRANSITIONS

__all__ = [
    "CHAINS",
    "CHAINS_MIN",
    "DIAGNOSTIC_THRESHOLDS",
    "DIVERGENCES_MAX",
    "DRAWS_PER_CHAIN",
    "EBFMI_MIN",
    "ESS_BULK_MIN",
    "ESS_TAIL_MIN",
    "HELD_OUT_FRACTION",
    "LIFECYCLE_TRANSITIONS",
    "MAX_TREEDEPTH_HITS",
    "MONITORED_PARAMETERS",
    "MONITORED_VECTOR_FAMILIES",
    "REPRODUCTION_PREDICTIVE_ESS_FRACTION_MIN",
    "REPRODUCTION_TOLERANCE_DAYS",
    "RUN_SHAPE_SQL",
    "R_HAT_MAX",
    "SPLIT_SEED",
    "TUNING_DRAWS_PER_CHAIN",
    "ConfigError",
    "DiagnosticThreshold",
    "RunShape",
    "blocking_diagnostics",
    "monitored_parameter_names",
    "read_run_shape",
    "split_seed_entropy",
]


class ConfigError(RuntimeError):
    """Raised when the environment contradicts a value published here.

    A `RuntimeError` rather than a `ValueError`: nothing a caller passed is
    wrong. The database was asked for the run shape these constants are stated
    against and did not answer with one.
    """


# ---------------------------------------------------------------------------
# Diagnostic thresholds — spec.md § Published Constants
# ---------------------------------------------------------------------------

#: **Blocking diagnostic.** Convergence convention at four or more chains.
R_HAT_MAX = 1.01

#: **Blocking diagnostic.** 100 x chain count at the four-chain minimum. A fixed
#: floor, *not* a function of the realized chain count: FR-035 sets a minimum and
#: permits more, so at eight chains the basis would imply 800 while this constant
#: says 400. The constant governs; the basis records where the number came from.
ESS_BULK_MIN = 400

#: **Blocking diagnostic.** The same bar applied to the tail.
ESS_TAIL_MIN = 400

#: **Blocking diagnostic.** Contested in general; zero is the defensible choice
#: at this dataset size, and publishing a number is what makes the gate
#: falsifiable.
DIVERGENCES_MAX = 0

#: **Blocking diagnostic.** Nominal threshold.
EBFMI_MIN = 0.3

#: **Blocking precondition** — knowable before sampling, so it refuses before
#: sampling starts (FR-035) rather than after it (FR-017). Also the basis the
#: R-hat and ESS thresholds above are stated at.
CHAINS_MIN = 4

#: **Reported, never blocking** (FR-018). An efficiency concern, not a validity
#: one. The number is published anyway because `forecast_diagnostic
#: .threshold_value` is NOT NULL and `ck_forecast_diagnostic__passed_matches_
#: threshold` computes `passed` against it: at 0 with direction `max`,
#: `passed = false` means the sampler hit the cap and the run still ships.
MAX_TREEDEPTH_HITS = 0


@dataclass(frozen=True, slots=True)
class DiagnosticThreshold:
    """One published bar, in the vocabulary `forecast_diagnostic` stores it in.

    The four fields are the row's own columns, so a writer copies rather than
    re-derives: `ck_forecast_diagnostic__direction_matches_metric`,
    `…__metric_matches_scope` and `…__blocking_matches_metric` each pin one of
    them to the metric, and a value assembled at the call site would be a second
    opinion the database then rejects.
    """

    metric: str
    threshold_value: float
    threshold_direction: str
    diagnostic_scope: str
    is_blocking: bool


#: The six metrics, in the order `0303`'s `ck_forecast_diagnostic__metric` lists
#: them. Five blocking diagnostics and one reported row — FR-017 and SC-014
#: quantify over the first group, and nothing quantifies over the second.
DIAGNOSTIC_THRESHOLDS: tuple[DiagnosticThreshold, ...] = (
    DiagnosticThreshold("r_hat", float(R_HAT_MAX), "max", "parameter", True),
    DiagnosticThreshold("ess_bulk", float(ESS_BULK_MIN), "min", "parameter", True),
    DiagnosticThreshold("ess_tail", float(ESS_TAIL_MIN), "min", "parameter", True),
    DiagnosticThreshold("divergent_transitions", float(DIVERGENCES_MAX), "max", "run", True),
    DiagnosticThreshold("ebfmi", float(EBFMI_MIN), "min", "run", True),
    DiagnosticThreshold("max_treedepth_hits", float(MAX_TREEDEPTH_HITS), "max", "run", False),
)


def blocking_diagnostics() -> tuple[DiagnosticThreshold, ...]:
    """The rows FR-017 refuses on, filtered rather than restated.

    A second tuple listing five of the six by hand is a second place for the
    treedepth row to be classified, and the one place a later metric would be
    added to only one of them.
    """
    return tuple(row for row in DIAGNOSTIC_THRESHOLDS if row.is_blocking)


# ---------------------------------------------------------------------------
# The split — AD-005, both values committed for the same reason
# ---------------------------------------------------------------------------

#: **Not a row class — committed configuration under AD-005.** Written to
#: `forecast_run.held_out_fraction_declared` on every run.
HELD_OUT_FRACTION = 0.25

#: **Committed for the same reason as the fraction, and an earlier revision of
#: AD-005 left it out.** A per-run seed lets a re-fit reshuffle the split until a
#: vendor lands favourably, which is FR-028's prohibition reached by another
#: route. With both fixed the split is a pure function of `(input_data_hash,
#: SPLIT_SEED, HELD_OUT_FRACTION)` (AD-011). Date-shaped, following E005's
#: `ROOT_SEED`, so a reader can see when it was fixed.
SPLIT_SEED = 20260727


def split_seed_entropy() -> str:
    """`SPLIT_SEED` in the form `forecast_run.split_seed_entropy` stores.

    Decimal digits as text, matching `ck_forecast_run__split_seed_format` and
    the delivered `seed_entropy` column beside it: 128 bits does not fit in
    `bigint` and nothing ever does arithmetic on the stored value.
    """
    return str(SPLIT_SEED)


# ---------------------------------------------------------------------------
# Reproduction — AD-004
# ---------------------------------------------------------------------------

#: **Tolerance.** A single absolute day tolerance on each line's median and 80th
#: percentile, pre-registered at Plan before any reproduction result existed and
#: never widened after seeing a comparison. Bounds an agreement check; refuses no
#: run, so neither FR-017 nor FR-035 reaches it.
REPRODUCTION_TOLERANCE_DAYS = 5.0

#: **The tolerance's basis condition, published beside it** (AD-004). The `n_eff`
#: the 5.0 days was derived at is the *predictive* effective sample size, not the
#: parameter ESS the gate floors at 400: each stored draw carries independent
#: residual and inverse-CDF randomness. Where a line's realized predictive ESS
#: falls below this fraction of the run's draw count, the comparison is reported
#: as **outside the tolerance's stated basis** rather than passing or failing.
REPRODUCTION_PREDICTIVE_ESS_FRACTION_MIN = 0.5


# ---------------------------------------------------------------------------
# Sampling shape — plan.md § Sampling shape
# ---------------------------------------------------------------------------

#: Four chains: the published minimum, run at the minimum. `CHAINS_MIN` is the
#: bar FR-035 refuses below and this is the value the job asks for; they are two
#: facts that happen to coincide, and collapsing them would leave the
#: precondition asserting a run's shape against itself.
CHAINS = 4

#: 4 x 1,000 = 4,000 post-warmup draws, which is the declared `draw_count`. The
#: product is deliberately **not** defined here: AD-009 has every run's draw
#: count compared against `schema_constants` read over the connection, and a
#: constant multiplying out to 4,000 in this module would be the fourth home for
#: a number that already has one.
DRAWS_PER_CHAIN = 1000

#: Recorded in the manifest as `tuning_count`; not a published constant, and no
#: requirement gates on it.
TUNING_DRAWS_PER_CHAIN = 1000


# ---------------------------------------------------------------------------
# The monitored parameter set — AD-006
# ---------------------------------------------------------------------------

#: The seven legal lifecycle edges, ordered. Sorted from the delivered set in
#: `model.procurement.lifecycle` rather than retyped: that module already mirrors
#: `fn_is_legal_lifecycle_transition`, and a third copy would be the defect
#: rather than the fix. `LEGAL_TRANSITIONS` is a `frozenset`, so it carries no
#: order of its own and one is imposed here — parameter names must be stable
#: across runs, and set iteration order is not a guarantee to rest that on.
LIFECYCLE_TRANSITIONS: tuple[tuple[str, str], ...] = tuple(sorted(LEGAL_TRANSITIONS))

_SOJOURN_KEYS = tuple(f"{source}__{target}" for source, target in LIFECYCLE_TRANSITIONS)

#: **The set the diagnostics gate quantifies over** (AD-006, FR-016): the
#: transition-level intercepts and log-scales of AD-001's sojourn model, the
#: hierarchical location and scale parameters, and the rework sub-model's
#: coefficients. **Never the per-line predictive draws** — those are constructed
#: from these by transformation plus fresh randomness, and their R-hat and ESS
#: are diluted by that randomness, which would let one unconverged parameter hide
#: behind ~68 well-behaved derived quantities.
#:
#: The bracket form is ArviZ's own flattening of a dimensioned variable, so this
#: tuple can be compared against an `az.summary` index directly rather than
#: through a naming translation. `model.py` and `design.py` name their graph
#: variables from here; this module is the authority FR-016 requires the set to
#: be named in.
MONITORED_PARAMETERS: tuple[str, ...] = (
    *(f"mu_sojourn[{key}]" for key in _SOJOURN_KEYS),
    *(f"sigma_sojourn[{key}]" for key in _SOJOURN_KEYS),
    "mu_population",
    "tau_vendor",
    "tau_category",
    "rework_intercept",
    "rework_approval_cycle_beta",
)

#: The two monitored families whose members are indexed by the *data* — one
#: offset per vendor and one per material category. They are fitted parameters
#: and so are inside AD-006's set, but their members cannot be a committed
#: constant without hard-coding a dataset fact into published configuration.
#: `monitored_parameter_names()` enumerates them from the run's own index.
#:
#: They widen the set past AD-006's literal three groups, and the cost is a
#: number worth stating: at the committed dataset's 12 vendors and 20 material
#: categories the monitored set is 51 parameters and `forecast_diagnostic` takes
#: 156 rows per run, against the "order of 80" `data-model.md` § Volumetrics
#: sizes the table at. Storage is trivial either way; what the widening buys is
#: that a vendor offset which did not converge refuses the run instead of being
#: published as a shrinkage weight nobody monitored.
MONITORED_VECTOR_FAMILIES: tuple[str, ...] = ("vendor_offset", "category_offset")


def monitored_parameter_names(
    vendor_ids: tuple[str, ...], material_categories: tuple[str, ...]
) -> tuple[str, ...]:
    """The complete monitored set for one run, enumerated rather than described.

    FR-016 requires the set to be *named*, and DV-011 asserts that no parameter
    is partially covered — both need an enumeration, which is what this returns:
    the committed scalar parameters followed by one member per vendor and one per
    material category, each in the caller's order. The caller passes the index
    `design.py` built, so the monitored set and the fitted graph cannot disagree
    about which vendors exist.
    """
    if not vendor_ids or not material_categories:
        raise ValueError(
            "the monitored set is enumerated over the run's own vendor and material "
            "category index; an empty index would silently monitor neither hierarchy"
        )
    return (
        *MONITORED_PARAMETERS,
        *(f"vendor_offset[{vendor_id}]" for vendor_id in vendor_ids),
        *(f"category_offset[{category}]" for category in material_categories),
    )


# ---------------------------------------------------------------------------
# The run shape — read, never written here (AD-009)
# ---------------------------------------------------------------------------


class RunShape(NamedTuple):
    """The declared draw count and grid horizon, as the database publishes them."""

    draw_count: int
    horizon_days: int


#: Module-level SQL, never assembled from values (Ruff S608). `schema_constants`
#: is a singleton by `ck_schema_constants__singleton`, so no predicate is needed
#: and the row count is an assertion rather than a filter.
RUN_SHAPE_SQL = "SELECT draw_count, survival_horizon_days FROM schema_constants"


def read_run_shape(connection: Connection | Session) -> RunShape:
    """The run shape, read over the connection — never a literal (AD-009).

    E003 declares 4,000 draws over a 365-day horizon on its `schema_constants`
    row and `/src/api` serves that horizon to readers, so those are the numbers
    a run must honour. Comparing against the published row rather than against
    literals is what keeps this from becoming a fourth copy of a constant that
    already has a home — and a `CHECK` could not do the job either, since E003's
    own suite passes runs at 5 draws over a 3-day horizon deliberately.
    """
    rows = connection.execute(text(RUN_SHAPE_SQL)).all()
    if len(rows) != 1:
        raise ConfigError(
            f"`schema_constants` returned {len(rows)} rows; it is a singleton by "
            f"`ck_schema_constants__singleton`, so the run shape E007 pins itself to "
            f"cannot be read from this database. Bring it up with "
            f"`uv run --directory src/model migrate`."
        )
    draw_count, horizon_days = rows[0]
    return RunShape(draw_count=int(draw_count), horizon_days=int(horizon_days))
