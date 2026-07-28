"""T029's sampler: where its shape comes from, what it refuses, and one real run.

Test-after, per `tasks.md` — `sample.py` is not in the property tier. Four
claims, and each is about a default rather than about NUTS: the shape is
`config.py`'s and not a second copy of it, `random_seed` is required rather than
defaulted, `progressbar` is off because this entry does not declare matplotlib,
and a real (tiny) fit returns an `InferenceData` whose posterior carries every
name `monitored_parameter_names` enumerates — the property AD-012 put at risk by
making `vendor_offset` a `Deterministic` derived from a monitored `z`.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import tomllib
from pathlib import Path

import arviz as az
import numpy as np
import pytest

# The tier's committed cohort, imported rather than re-authored: six lines
# covering every branch the contribution expression can take, against a roster
# small enough to fit in a fifty-draw run. A second cohort here would be a
# second opinion about what a valid frame looks like, and this module has no
# claim to make about that. `tests/forecast/__init__.py` is what makes the
# sibling importable as `forecast.<name>` (see this tier's conftest).
from forecast.test_model_logp import AS_OF, CATEGORIES, VENDORS, frame_over
from model.forecast.config import (
    CHAINS,
    DRAWS_PER_CHAIN,
    TUNING_DRAWS_PER_CHAIN,
    monitored_parameter_names,
)
from model.forecast.model import build_model
from model.forecast.sample import SampleError, sample_posterior

#: `src/model/tests/forecast/` -> `src/model/`.
ENTRY_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_SOURCE = ENTRY_ROOT / "src" / "model" / "forecast" / "sample.py"
ENTRY_MANIFEST = ENTRY_ROOT / "pyproject.toml"

#: Where the three shape defaults must come from. Naming the module rather than
#: only the symbols is what makes the assertion "from `config.py`" instead of
#: "from something that happens to be spelled `CHAINS`".
CONFIG_MODULE = "model.forecast.config"

#: Default expression -> the constant it must name, for each shape argument.
SHAPE_DEFAULTS: dict[str, tuple[str, int]] = {
    "chains": ("CHAINS", CHAINS),
    "draws": ("DRAWS_PER_CHAIN", DRAWS_PER_CHAIN),
    "tune": ("TUNING_DRAWS_PER_CHAIN", TUNING_DRAWS_PER_CHAIN),
}

#: The plotting stack PyMC's rich progress backend pulls in. `sample.py` turns
#: the progress bar off by default because of it, so the reason is asserted
#: rather than left in a docstring.
PLOTTING_PACKAGE = "matplotlib"

#: Small enough that the whole module costs one PyTensor compile and a few
#: seconds of sampling, large enough that ArviZ produces a summary. Nothing here
#: is a convergence claim: the diagnostics gate runs at the published shape, and
#: a fifty-draw fit asserting an R-hat would be inventing a bar.
TINY_CHAINS = 2
TINY_DRAWS = 50

#: The container `pm.sample(return_inferencedata=True)` hands back, named by
#: asking ArviZ to build the smallest one rather than by importing a class.
#:
#: **`az.InferenceData` no longer exists.** ArviZ 1.x moved the role to
#: `xarray.DataTree` and left the old attribute as a shim that raises a
#: `MigrationWarning` on access, so `isinstance(x, az.InferenceData)` warns and a
#: return annotation naming it fails `typing.get_type_hints` under `-W error`.
#: `sample.py` annotated it that way and survived only because the annotation was
#: deferred and never evaluated; it now annotates `xarray.DataTree` behind a
#: `TYPE_CHECKING` guard, so no runtime dependency on xarray is implied. Asserting
#: on the real type here is what keeps this record from going stale.
INFERENCE_DATA_TYPE = type(az.convert_to_datatree({"x": np.zeros((1, 1))}))


def _sample_posterior_ast() -> ast.FunctionDef:
    """`sample_posterior`'s definition, parsed from the source on disk."""
    tree = ast.parse(SAMPLE_SOURCE.read_text(encoding="utf-8"))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "sample_posterior"
    ]
    assert len(functions) == 1, (
        f"expected exactly one `sample_posterior` in {SAMPLE_SOURCE.name}, found "
        f"{len(functions)}; every assertion below reads the first one."
    )
    return functions[0]


def _keyword_defaults() -> dict[str, ast.expr | None]:
    """Each keyword-only argument mapped to its default expression, or `None`.

    `None` here is the AST's way of saying *no default* — which is the shape
    `random_seed` has to have and the shape a defaulted seed would not.
    """
    definition = _sample_posterior_ast()
    return {
        argument.arg: default
        for argument, default in zip(
            definition.args.kwonlyargs, definition.args.kw_defaults, strict=True
        )
    }


# ---------------------------------------------------------------------------
# The shape is `config.py`'s, and is not restated here
# ---------------------------------------------------------------------------


def test_the_module_imports_the_shape_constants_from_config() -> None:
    """The three constants come from `model.forecast.config` and nowhere else.

    Checked on the import statement rather than only on the values, because the
    values would agree just as well with a literal that happened to match today.
    """
    tree = ast.parse(SAMPLE_SOURCE.read_text(encoding="utf-8"))
    imported = {
        alias.name: node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    for symbol, _ in SHAPE_DEFAULTS.values():
        assert imported.get(symbol) == CONFIG_MODULE, (
            f"{symbol!r} is imported from {imported.get(symbol)!r}, not {CONFIG_MODULE!r}. "
            f"The declared `draw_count` and the shape actually asked for have to be one "
            f"fact; a second home for either is how they drift apart."
        )


@pytest.mark.parametrize("argument", sorted(SHAPE_DEFAULTS))
def test_each_shape_default_is_the_config_constant_rather_than_a_literal(argument: str) -> None:
    """`chains`, `draws` and `tune` default to a name, and to the right value.

    Two halves, and the first is the one that survives a change: a literal `4`
    would pass a value comparison on the day it was written and go stale the day
    `CHAINS` moved. So the default expression is required to be a *reference*,
    and only then is the value it resolves to compared.
    """
    symbol, expected = SHAPE_DEFAULTS[argument]
    default = _keyword_defaults()[argument]

    assert isinstance(default, ast.Name), (
        f"`{argument}` defaults to {ast.dump(default) if default else '(nothing)'}; it must "
        f"default to the name {symbol!r} so the published shape has one home (`config.py`)."
    )
    assert default.id == symbol, f"`{argument}` defaults to {default.id!r}, not {symbol!r}"

    bound = inspect.signature(sample_posterior).parameters[argument].default
    assert bound == expected, (
        f"`{argument}` resolves to {bound!r} but `config.{symbol}` is {expected!r}; the "
        f"import above is satisfied and the values still disagree, which means the module "
        f"rebinds the name after importing it."
    )


# ---------------------------------------------------------------------------
# The seed is required, not defaulted
# ---------------------------------------------------------------------------


def test_random_seed_is_keyword_only_and_carries_no_default() -> None:
    """A caller must state the seed; the module may not choose one for them.

    `forecast_run.seed_entropy` records the seed of every published run, so a
    default here would put an unrecorded constant behind every one of them — and
    it would be invisible, because the recorded value and the used value would
    still agree. Keyword-only as well as required: at a positional second
    parameter, a caller passing a chain count would seed the run with it.
    """
    parameter = inspect.signature(sample_posterior).parameters["random_seed"]

    assert parameter.default is inspect.Parameter.empty, (
        f"`random_seed` defaults to {parameter.default!r}. The seed is what makes a re-fit "
        f"a re-fit; defaulting it puts an unrecorded constant behind every published run."
    )
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert _keyword_defaults()["random_seed"] is None, (
        "the source carries a default for `random_seed` that the signature does not show"
    )


def test_omitting_the_seed_is_a_type_error_before_any_sampling_happens() -> None:
    """The refusal is Python's, so it costs nothing and cannot be reached past."""
    with pytest.raises(TypeError, match="random_seed"):
        sample_posterior(build_model(frame_over()))  # type: ignore[call-arg]


def test_an_explicitly_none_seed_is_refused_by_name() -> None:
    """`random_seed=None` is the one way past the signature, and it is closed.

    PyMC accepts `None` and draws a seed from entropy, which produces a run
    nobody can reproduce and no error anybody sees. `SampleError` names the
    caller's mistake instead.
    """
    with pytest.raises(SampleError, match="seed"):
        sample_posterior(build_model(frame_over()), random_seed=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The progress bar is off, and the reason is a fact about this entry
# ---------------------------------------------------------------------------


def test_progressbar_defaults_to_off() -> None:
    """Off by default: PyMC's rich backend imports a package this entry lacks."""
    default = _keyword_defaults()["progressbar"]

    assert isinstance(default, ast.Constant) and default.value is False, (
        f"`progressbar` defaults to {ast.dump(default) if default else '(nothing)'}, not `False`."
    )
    assert inspect.signature(sample_posterior).parameters["progressbar"].default is False


def test_the_entry_declares_no_plotting_stack_which_is_why_the_default_is_off() -> None:
    """The reason, asserted rather than described.

    Two directions, because either alone is weak. `matplotlib` is absent from
    this entry's declared dependencies — E007 adds none, so it cannot be added
    to turn the bar on — *and* it is absent from the resolved environment, so
    `progressbar=True` really would raise from inside the sampler rather than
    merely being undeclared while happening to work.

    If a later epic declares the package, this test fails and the docstring in
    `sample.py` explaining the default becomes false at the same moment. That is
    the intent: the default may then be reconsidered, deliberately.
    """
    manifest = tomllib.loads(ENTRY_MANIFEST.read_text(encoding="utf-8"))
    declared = [
        requirement
        for requirement in manifest["project"]["dependencies"]
        if requirement.split(">=")[0].split("==")[0].strip().lower() == PLOTTING_PACKAGE
    ]

    assert not declared, (
        f"{ENTRY_MANIFEST} now declares {declared}; `sample.py` turns the progress bar off "
        f"because it did not, and that reason has stopped being true."
    )
    assert importlib.util.find_spec(PLOTTING_PACKAGE) is None, (
        f"{PLOTTING_PACKAGE!r} resolves in this environment although the entry does not "
        f"declare it, so it arrived transitively. The default stays off either way — an "
        f"undeclared import is not one to depend on — but the stated reason is now weaker "
        f"than it reads."
    )


# ---------------------------------------------------------------------------
# One real run, at the smallest shape that produces a posterior
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tiny_posterior() -> object:
    """A real fit over the tier's cohort: two chains, fifty draws, fifty tuning.

    Module-scoped because the PyTensor compile is the expensive part and every
    assertion below wants the same object. `cores=1` on purpose — the default is
    one worker per chain, which spawns processes on Windows and deadlocks from
    an unguarded import, which is precisely the trap `sample.py`'s docstring
    warns a bare script about.
    """
    return sample_posterior(
        build_model(frame_over()),
        random_seed=20260727,
        chains=TINY_CHAINS,
        draws=TINY_DRAWS,
        tune=TINY_DRAWS,
        cores=1,
    )


def test_a_tiny_run_returns_inferencedata_with_the_sampler_statistics(
    tiny_posterior: object,
) -> None:
    """An `InferenceData` at the requested shape, carrying `sample_stats`.

    The two run-scope diagnostics FR-017 refuses on — divergences and E-BFMI —
    are read from `diverging` and `energy`, so a container holding draws without
    them would let a run be summarised on the parameter metrics alone.
    """
    assert isinstance(tiny_posterior, INFERENCE_DATA_TYPE)
    assert tiny_posterior.posterior.sizes["chain"] == TINY_CHAINS
    assert tiny_posterior.posterior.sizes["draw"] == TINY_DRAWS

    assert {"posterior", "sample_stats"} <= set(tiny_posterior.children)
    for statistic in ("diverging", "energy"):
        assert statistic in tiny_posterior.sample_stats.data_vars, (
            f"`sample_stats` carries no {statistic!r}, so the run-scope half of the "
            f"diagnostics gate has nothing to read."
        )


def test_the_posterior_carries_every_monitored_parameter(
    tiny_posterior: object,
) -> None:
    """Every name `config.py` enumerates resolves in an `az.summary` index (FR-016).

    This is the assertion AD-012 needs. `vendor_offset` is now a `Deterministic`
    rather than a sampled vector, and `vendor_offset_z` is the coordinate the
    sampler actually moves; the monitored set names both, and a graph that
    dropped either — or spelled one differently — would leave the gate
    quantifying over a parameter no run reports. Compared against the summary
    index rather than against `posterior.data_vars`, because the index is the
    flattened, bracketed form the gate reads and the data_vars are not.
    """
    monitored = monitored_parameter_names(VENDORS, CATEGORIES)
    summarised = set(az.summary(tiny_posterior, round_to="none").index)

    assert not set(monitored) - summarised, (
        f"the summary index is missing {sorted(set(monitored) - summarised)}. Every "
        f"monitored name must resolve there: FR-016 makes the gate quantify over the "
        f"enumerated set, and a name with no row is a parameter nobody checked."
    )
    for required in ("tau_vendor", "vendor_offset_z[VND-0001]", "vendor_offset[VND-0001]"):
        assert required in monitored, (
            f"{required!r} is not in the monitored set. AD-012 keeps the sampled `z`, its "
            f"scale, and the derived offset all monitored — R-hat on the product can look "
            f"healthy while the coordinate it derives from has not mixed."
        )


def test_the_anchor_the_cohort_was_built_at_is_the_one_this_module_states() -> None:
    """A guard on the imported fixture, not a claim about the sampler.

    `frame_over` is another module's helper; if its anchor moved, this module's
    fit would silently change shape. Cheap to state, and it keeps the borrowed
    cohort from becoming an unexamined dependency.
    """
    frame = frame_over()
    assert AS_OF.isoformat() == "2025-12-01"
    assert frame.vendor_ids == VENDORS
    assert frame.material_categories == CATEGORIES
