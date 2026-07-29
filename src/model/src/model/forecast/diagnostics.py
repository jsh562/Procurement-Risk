"""The six monitored metrics, judged against the bars `config.py` publishes.

FR-016 monitors R-hat, bulk and tail effective sample size, divergent
transitions and E-BFMI over the named parameter set; FR-018 records maximum
treedepth hits as **reported and never blocking**, with its published bar of 0
so `ck_forecast_diagnostic__passed_matches_threshold` has a number to compute
`passed` against. Nothing here refuses anything — this module assembles rows and
names the breaches, and `fit.py` is where the refusal happens, because the
refusal guarantee is ordering rather than rollback (AD-010).

`passed` is arithmetic, in exactly the shape `0303` re-derives it in, so a row
assembled here and the row the database checks cannot disagree. The gate is a
strictly stronger predicate: a blocking metric that could not be *measured* is
not a metric that passed (`plan.md` § Error Handling Strategy).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from model.forecast.config import DIAGNOSTIC_THRESHOLDS, DiagnosticThreshold

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation only
    import xarray as xr

__all__ = [
    "DIVERGING_STATISTIC",
    "ENERGY_STATISTIC",
    "PARAMETER_METRICS",
    "RUN_METRICS",
    "TREEDEPTH_STATISTIC",
    "DiagnosticRow",
    "DiagnosticsError",
    "blocking_breaches",
    "diagnostic_row",
    "direction_prose",
    "evaluate_diagnostics",
    "monitored_parameter_coverage",
    "passes",
    "refusal_lines",
    "threshold_for",
]


class DiagnosticsError(ValueError):
    """Raised when a diagnostic cannot be assembled from what the caller gave.

    A `ValueError`, following `SampleError` and `PosteriorError`: every case is
    an argument — a metric outside the published six, a parameter name on a
    run-scope metric or missing from a parameter-scope one, a posterior with no
    sampler statistics. A *breach* is not an error and never raises here; it is
    a row with `passed` false, which is a measurement rather than a fault.
    """


#: Metric -> its published bar, keyed for lookup. Derived from the published
#: tuple rather than restated, so a metric added to `config.py` and not here is
#: impossible rather than merely unlikely.
_THRESHOLD_BY_METRIC: dict[str, DiagnosticThreshold] = {
    row.metric: row for row in DIAGNOSTIC_THRESHOLDS
}

#: The three metrics `ck_forecast_diagnostic__metric_matches_scope` confines to
#: parameter scope, and the three it confines to run scope. Filtered from the
#: published tuple for the reason `blocking_diagnostics()` is filtered: two
#: hand-written lists are two places for a seventh metric to be classified.
PARAMETER_METRICS: tuple[str, ...] = tuple(
    row.metric for row in DIAGNOSTIC_THRESHOLDS if row.diagnostic_scope == "parameter"
)
RUN_METRICS: tuple[str, ...] = tuple(
    row.metric for row in DIAGNOSTIC_THRESHOLDS if row.diagnostic_scope == "run"
)

#: The `sample_stats` variables the three run-scope metrics are read from.
#: `diverging` is one boolean per draw, `energy` one float per draw — E-BFMI is
#: computed from it per chain — and `reached_max_treedepth` one boolean per
#: draw. Named here rather than inline so `test_sample.py`'s assertion that the
#: container carries them and this module's reads name the same strings.
DIVERGING_STATISTIC = "diverging"
ENERGY_STATISTIC = "energy"
TREEDEPTH_STATISTIC = "reached_max_treedepth"

#: The three columns `az.summary` publishes the parameter-scope metrics under,
#: mapped to this epic's metric names. ArviZ spells R-hat `r_hat`, which happens
#: to match; the mapping is written out anyway so a future rename is a one-line
#: change here rather than a silent `KeyError` at the gate.
_SUMMARY_COLUMN: dict[str, str] = {
    "r_hat": "r_hat",
    "ess_bulk": "ess_bulk",
    "ess_tail": "ess_tail",
}


@dataclass(frozen=True, slots=True)
class DiagnosticRow:
    """One measured diagnostic, in the vocabulary `forecast_diagnostic` stores it.

    Every field is a column of the table, so the writer copies rather than
    re-derives. `passed` is computed at construction from the row's own two
    numbers — a caller cannot supply it, which is the Python-side counterpart of
    `passed` being arithmetic rather than an opinion in the schema.
    """

    metric: str
    parameter_name: str | None
    observed_value: float
    threshold_value: float
    threshold_direction: str
    diagnostic_scope: str
    is_blocking: bool
    passed: bool

    @property
    def is_computable(self) -> bool:
        """Whether the value is one `ck_forecast_diagnostic__observed_finite` admits.

        A NaN or an infinity is not a measurement that failed; it is a
        measurement that did not happen, and the two are reported differently.
        The stored column rejects all three values, so a row failing this can
        never be written — which is why the gate has to see it first.
        """
        return math.isfinite(self.observed_value)

    @property
    def breached(self) -> bool:
        """Whether this row refuses the run: blocking, and not a measured pass.

        Two conditions rather than one. `passed` alone would let `+inf` clear a
        floor, and `is_computable` alone would let a measured breach through.
        Only blocking rows can refuse anything — treedepth is reported.
        """
        return self.is_blocking and not (self.passed and self.is_computable)

    def described(self) -> str:
        """FR-017's five-field set for this row, as one line.

        The metric, the `parameter_name` where the metric is parameter-scoped,
        the realized value, the threshold and the threshold's **direction** —
        without which a value and a bar do not resolve to a verdict for a reader
        who does not already know which metrics are floors and which are
        ceilings. FR-038's unit, with the refusal itself as the verdict.
        """
        scope = f" on `{self.parameter_name}`" if self.parameter_name is not None else ""
        realized = (
            f"{self.observed_value:g}"
            if self.is_computable
            else f"{self.observed_value} (uncomputable — not out of range)"
        )
        return (
            f"{self.metric}{scope}: realized {realized} against a threshold of "
            f"{self.threshold_value:g}, direction {self.threshold_direction} "
            f"({direction_prose(self.threshold_direction)}) — breached"
        )

    def row_parameters(self, run_id: uuid.UUID) -> dict[str, object]:
        """This row's bind parameters for `forecast_diagnostic`.

        `diagnostic_id` is drawn here rather than by the writer: the natural key
        is `(run_id, metric, parameter_name)` and the surrogate exists only
        because a primary key admits no null, so nothing downstream reads it and
        nothing needs it to be assembled anywhere in particular.
        """
        return {
            "diagnostic_id": uuid.uuid4(),
            "run_id": run_id,
            "diagnostic_scope": self.diagnostic_scope,
            "parameter_name": self.parameter_name,
            "metric": self.metric,
            "observed_value": float(self.observed_value),
            "threshold_value": float(self.threshold_value),
            "threshold_direction": self.threshold_direction,
            "is_blocking": self.is_blocking,
            "passed": self.passed,
        }


#: How each direction reads in prose, so the refusal message and the refusal
#: report say the same thing about the same bar.
_DIRECTION_PROSE: dict[str, str] = {
    "max": "the realized value must be at or below it",
    "min": "the realized value must be at or above it",
}


def direction_prose(threshold_direction: str) -> str:
    """What a direction means, spelled out once for both refusal surfaces.

    FR-017 requires the direction in the refusal message and DV-038 requires the
    same field set in the emitted report. Rendering the sentence in two places
    is how they drift: the stream kept the threshold and dropped its direction
    once already, which is why FR-038 states the unit rather than the verb.
    """
    try:
        return _DIRECTION_PROSE[threshold_direction]
    except KeyError as exc:
        raise DiagnosticsError(
            f"{threshold_direction!r} is not a direction "
            f"`ck_forecast_diagnostic__direction` admits; a value and a bar do not "
            f"resolve to a verdict without one of `max` or `min`"
        ) from exc


def threshold_for(metric: str) -> DiagnosticThreshold:
    """The published bar for `metric`, or a refusal naming the closed set.

    Closed rather than defaulted, mirroring `ck_forecast_diagnostic__metric`: a
    seventh metric acquiring a bar nobody published would make FR-017 refuse
    against a threshold that is not in Published Constants, which is the one
    thing a published gate must not do.
    """
    try:
        return _THRESHOLD_BY_METRIC[metric]
    except KeyError as exc:
        raise DiagnosticsError(
            f"{metric!r} is not one of the six metrics `ck_forecast_diagnostic__metric` "
            f"admits ({', '.join(_THRESHOLD_BY_METRIC)}); a metric with no published "
            f"threshold has no verdict to record"
        ) from exc


def passes(threshold: DiagnosticThreshold, observed_value: float) -> bool:
    """`ck_forecast_diagnostic__passed_matches_threshold`, in Python.

    `max` is `observed <= threshold` and `min` is `observed >= threshold`, so
    the boundary passes on both — the zero-divergence bar every clean run sits
    on is a pass, not a breach. NaN compares false either way, which is the
    right verdict reported for the wrong reason; `DiagnosticRow.is_computable`
    is what names the actual defect.
    """
    if threshold.threshold_direction == "max":
        return observed_value <= threshold.threshold_value
    return observed_value >= threshold.threshold_value


def diagnostic_row(
    metric: str, observed_value: float, parameter_name: str | None = None
) -> DiagnosticRow:
    """One row: the published bar, the realized value, and the verdict between them.

    The scope, direction and blocking flag come from `config.py` and never from
    the caller — each is pinned to the metric by one of `0303`'s agreement
    checks, and a value assembled at the call site would be a second opinion the
    database then rejects. `parameter_name` is required exactly at parameter
    scope and refused elsewhere, which is
    `ck_forecast_diagnostic__parameter_iff_parameter_scope`.
    """
    threshold = threshold_for(metric)
    scoped = threshold.diagnostic_scope == "parameter"
    if scoped and not (parameter_name or "").strip():
        raise DiagnosticsError(
            f"{metric} is measured per parameter and was given no parameter name; "
            f"`r_hat`, `ess_bulk` and `ess_tail` are keyed by parameter in the diagnostics "
            f"store, and a bare metric name does not say which parameter breached"
        )
    if not scoped and parameter_name is not None:
        raise DiagnosticsError(
            f"{metric} is measured over the whole run and was given the parameter name "
            f"{parameter_name!r}; a per-parameter divergence count is not a quantity, and "
            f"`ck_forecast_diagnostic__parameter_iff_parameter_scope` refuses the row"
        )
    value = float(observed_value)
    return DiagnosticRow(
        metric=metric,
        parameter_name=parameter_name,
        observed_value=value,
        threshold_value=threshold.threshold_value,
        threshold_direction=threshold.threshold_direction,
        diagnostic_scope=threshold.diagnostic_scope,
        is_blocking=threshold.is_blocking,
        passed=passes(threshold, value),
    )


def blocking_breaches(rows: Iterable[DiagnosticRow]) -> tuple[DiagnosticRow, ...]:
    """**Every** breached blocking row, in the order they were measured.

    Every one and not the first: several rows can breach in a single run, and an
    operator handed one of them returns for a second run to discover the next
    (FR-017). The treedepth row is excluded here by its own `is_blocking`
    rather than by name, so FR-018's classification lives in one place.
    """
    return tuple(row for row in rows if row.breached)


def refusal_lines(breaches: Sequence[DiagnosticRow]) -> tuple[str, ...]:
    """One complete five-field set per breach, ready for standard error.

    Returned as lines rather than as one string so the caller decides the
    framing — `fit.py` puts them in a `FitError` message and `report.py` renders
    the same rows into the emitted file, which is what makes DV-038's "the same
    field set as the stderr reason" a shared derivation rather than a promise.
    """
    return tuple(row.described() for row in breaches)


# ---------------------------------------------------------------------------
# Reading the six metrics off one fit
# ---------------------------------------------------------------------------


def _summary_frame(idata: xr.DataTree):
    """`az.summary`'s diagnostics frame, indexed by the flattened parameter name.

    Imported inside the function rather than at module scope so this module can
    be imported — and its comparison exercised by the property tier — without
    loading the modelling stack. `round_to="none"` because the default rounds
    ESS down to an integer and R-hat to two decimals, and a gate at 1.01
    comparing a value already rounded to 1.01 would pass a realized 1.0149.
    """
    import arviz as az

    return az.summary(idata, kind="diagnostics", round_to="none")


def _parameter_rows(idata: xr.DataTree, monitored_parameters: Sequence[str]) -> list[DiagnosticRow]:
    """Three rows for **every** monitored parameter, with no partial coverage.

    DV-011's completeness is a property of this loop: the three metrics are
    emitted together per parameter, so a parameter cannot acquire an R-hat row
    and lose its ESS one. A parameter the summary does not carry is recorded as
    a NaN rather than omitted — omitting it would fail DV-011, and `observed_
    value` is NOT NULL — and the gate then refuses it as uncomputable.
    """
    summary = _summary_frame(idata)
    index = set(summary.index)
    rows: list[DiagnosticRow] = []
    for parameter in monitored_parameters:
        for metric in PARAMETER_METRICS:
            value = (
                float(summary.at[parameter, _SUMMARY_COLUMN[metric]])
                if parameter in index
                else math.nan
            )
            rows.append(diagnostic_row(metric, value, parameter))
    return rows


def _sample_statistics(idata: xr.DataTree) -> xr.Dataset:
    """The `sample_stats` group, refused by name rather than by `AttributeError`.

    The three run-scope metrics live here and nowhere else, so a container
    holding draws without them would let a run be judged on the parameter
    metrics alone — which is a gate quantifying over three of its five blocking
    rows.
    """
    if "sample_stats" not in getattr(idata, "children", {}):
        raise DiagnosticsError(
            "the posterior carries no `sample_stats` group, so the divergence count, "
            "E-BFMI and the treedepth hits cannot be measured; FR-017 refuses on three "
            "run-scope metrics and a container without them cannot be judged"
        )
    return idata["sample_stats"].to_dataset()


def _run_rows(idata: xr.DataTree) -> list[DiagnosticRow]:
    """The three run-scope metrics, each read from its own sampler statistic.

    Divergences and treedepth hits are counts over every draw of every chain.
    E-BFMI is per chain and the **minimum** across them is what is recorded: the
    bar is a floor, and averaging would let one stuck chain hide behind three
    healthy ones — which is the failure the metric exists to detect.
    """
    import arviz as az

    statistics = _sample_statistics(idata)
    missing = [
        name
        for name in (DIVERGING_STATISTIC, ENERGY_STATISTIC, TREEDEPTH_STATISTIC)
        if name not in statistics.data_vars
    ]
    if missing:
        raise DiagnosticsError(
            f"`sample_stats` carries no {missing}; the run-scope half of the gate reads "
            f"{DIVERGING_STATISTIC!r}, {ENERGY_STATISTIC!r} and {TREEDEPTH_STATISTIC!r}, "
            f"and a metric that cannot be read is not a metric that passed"
        )
    per_chain = az.bfmi(idata)[ENERGY_STATISTIC].values
    return [
        diagnostic_row(
            "divergent_transitions", float(statistics[DIVERGING_STATISTIC].values.sum())
        ),
        diagnostic_row("ebfmi", float(min(per_chain)) if len(per_chain) else math.nan),
        diagnostic_row("max_treedepth_hits", float(statistics[TREEDEPTH_STATISTIC].values.sum())),
    ]


def evaluate_diagnostics(
    idata: xr.DataTree, monitored_parameters: Sequence[str]
) -> tuple[DiagnosticRow, ...]:
    """Every monitored diagnostic for one fit: three per parameter, three per run.

    The complete set rather than the breaching subset, because the same tuple is
    what transaction 1 stores on a run that ships (DV-011) and what the refusal
    reports on a run that does not — deriving the two separately would let the
    stored evidence and the refusal message describe different measurements.

    `monitored_parameters` is the enumeration `config.monitored_parameter_names`
    builds from the run's own vendor and category index (AD-006, FR-016); it is
    not re-derived here, so the gate and the fitted graph cannot disagree about
    which parameters exist.
    """
    names = tuple(monitored_parameters)
    if not names:
        raise DiagnosticsError(
            "the diagnostics gate was given an empty monitored set; FR-016 requires the "
            "set to be named, and a gate over no parameter passes every fit"
        )
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise DiagnosticsError(
            f"the monitored set names {duplicated} more than once; "
            f"`uq_forecast_diagnostic__run_metric_parameter` refuses the second row, and "
            f"a duplicated name would make DV-011's per-parameter count wrong"
        )
    return (*_parameter_rows(idata, names), *_run_rows(idata))


def monitored_parameter_coverage(
    rows: Iterable[DiagnosticRow],
) -> Mapping[str, frozenset[str]]:
    """Which parameter-scope metrics each named parameter carries.

    DV-011's own shape, exposed so the completeness assertion reads the rows
    rather than re-querying: a parameter mapping to fewer than the three
    `PARAMETER_METRICS` is partially covered, which no constraint can see
    because a `CHECK` admits no sibling row (G-7).
    """
    coverage: dict[str, set[str]] = {}
    for row in rows:
        if row.parameter_name is not None:
            coverage.setdefault(row.parameter_name, set()).add(row.metric)
    return {name: frozenset(metrics) for name, metrics in coverage.items()}
