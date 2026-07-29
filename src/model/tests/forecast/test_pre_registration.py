"""T077 — SC-032: nothing overrides `HELD_OUT_FRACTION` or `SPLIT_SEED` at run time.

FR-028 forbids adjusting a published band **or the held-out fraction** after a
result is seen. Risk 1 contemplates raising the fraction to make the registered
band measurable, and AD-005 answers by making both the fraction and the split
seed committed configuration: a per-run seed would let a re-fit reshuffle the
split until a vendor landed favourably, which is the same prohibition reached by
another route.

What a test can assert is the **mechanism** half — that the two values are fixed
at commit time and reachable by no run-time channel. Four closed doors, checked
here:

1. the job's argument parser declares no option that reaches either value;
2. no module in `model.forecast` reads an environment variable at all;
3. each constant is assigned exactly once in the package, at module level in
   `config.py`, from a literal — never computed, never re-bound;
4. the function that draws the split accepts no override parameter.

**The prohibition half is uncovered by any check, and this file says so rather
than implying otherwise.** G-11 records why: pre-registration is a fact about
*when* a value was fixed relative to a result, and no column and no assertion can
carry it — `held_out_fraction_declared` is a value each run writes, so a later run
can declare a different fraction and nothing in the schema objects. SC-032 is
verified by **review of the commit history** of `PKG/config.py`, because a change
to a committed constant is a diff with an author and a date. The last test below
demonstrates the gap instead of papering over it: it shows the constant is
authoritative — change it and the realized split follows — which is exactly why
only the history distinguishes pre-registration from post-hoc adjustment.

A green run of this file therefore evidences that the door is closed. It does not
evidence that nobody walked through it before it was closed, and a suite that
read as though it did would be the more dangerous artifact.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from forecast.conftest import EmittedRun, FitInput
from model.forecast import config
from model.forecast import split as split_module
from model.forecast.config import HELD_OUT_FRACTION, SPLIT_SEED
from model.forecast.fit import _parser
from model.forecast.split import HELD_OUT, assign_split
from model.schema.url import DATABASE_URL_ENV_VAR

#: The two committed constants SC-032 protects, by the name they are bound under.
PRE_REGISTERED_CONSTANTS = ("HELD_OUT_FRACTION", "SPLIT_SEED")

#: The job's whole option surface, as argparse destinations. An equality rather
#: than an absence test: "no option named `--held-out-fraction`" would pass on a
#: `--split-config` that carried one, and the point is that the surface is small
#: enough to be enumerated and every member of it is a shape or an anchor.
DECLARED_OPTIONS = frozenset(
    {"as_of_date", "seed", "chains", "draws", "tune", "cores", "report_root"}
)

#: The minimum argument vector the parser accepts, so `parse_args` can be used to
#: read the destination set through the public interface rather than through
#: `_actions`.
MINIMAL_ARGV = ["--as-of-date", "2026-04-01"]

#: Where the package's source lives, walked as text for the AST assertions.
PACKAGE_ROOT = Path(config.__file__).resolve().parent

#: Every way a module reads the environment. Names rather than a text search, so
#: `from os import environ` is caught alongside `os.environ`.
ENVIRONMENT_READERS = ("environ", "getenv", "environb")

#: The fraction the demonstration at the end re-draws the split at. Materially
#: different from the committed 0.25, so the realized proportion cannot agree
#: with both.
DEMONSTRATION_FRACTION = 0.5


def package_modules() -> tuple[Path, ...]:
    """Every source file of `model.forecast`, in a stable order.

    Walked from the package directory rather than listed, so a module added
    later is covered without anyone remembering to add it here — which is the
    only way a source-level absence check stays true.
    """
    modules = tuple(sorted(PACKAGE_ROOT.glob("*.py")))
    assert len(modules) > 1, f"no package source found under {PACKAGE_ROOT}"
    return modules


def parsed(module: Path) -> ast.Module:
    """One module's syntax tree, parsed from the file on disk."""
    return ast.parse(module.read_text(encoding="utf-8"), filename=str(module))


def assignments_to(tree: ast.Module, name: str) -> list[ast.expr]:
    """Every value assigned to `name` anywhere in a module, at any depth.

    Walked over the whole tree rather than over module-level statements only: a
    constant re-bound inside a function is exactly the run-time override this
    file exists to refuse, and looking only at the top level would miss it.
    """
    values: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            values.append(node.value)
    return values


# ---------------------------------------------------------------------------
# Door 1 — the command line
# ---------------------------------------------------------------------------


def test_the_job_declares_no_option_that_could_reach_either_constant() -> None:
    """SC-032's flag half, as an equality over the whole option surface.

    Read through `parse_args` rather than through argparse's private action
    list, so this asserts what a caller can actually set. Every declared option
    is either the run's anchor, its entropy, its shape or where the report goes;
    none of them is the split.
    """
    destinations = set(vars(_parser().parse_args(MINIMAL_ARGV)))

    assert destinations == set(DECLARED_OPTIONS)
    for constant in PRE_REGISTERED_CONSTANTS:
        assert constant.lower() not in destinations


def test_the_parsers_own_documentation_records_why_the_two_are_absent() -> None:
    """The reason travels with the code, not only with this file.

    A future author adding a `--held-out-fraction` flag reads the function they
    are editing, not the test that will fail. The docstring names AD-005 and
    SC-032 so the prohibition is legible at the point of the change.
    """
    documentation = inspect.getdoc(_parser) or ""

    assert all(constant in documentation for constant in PRE_REGISTERED_CONSTANTS)
    assert "SC-032" in documentation


# ---------------------------------------------------------------------------
# Door 2 — the environment
# ---------------------------------------------------------------------------


def test_no_module_in_the_package_reads_an_environment_variable() -> None:
    """The env half of SC-032, asserted over the source rather than by trying spellings.

    An environment override is the channel a constant is most easily reached by
    and the one that leaves no diff. No module here reads one at all, so there
    is no variable name to guess and no precedence order to reason about.
    """
    offenders = [
        f"{module.name}:{node.lineno}"
        for module in package_modules()
        for node in ast.walk(parsed(module))
        if (isinstance(node, ast.Attribute) and node.attr in ENVIRONMENT_READERS)
        or (isinstance(node, ast.Name) and node.id in ENVIRONMENT_READERS)
    ]

    assert offenders == [], (
        f"{offenders} read the environment inside `model.forecast`. Every published constant "
        f"in this package is committed configuration, and an environment channel is a "
        f"per-invocation override with no author and no date (SC-032, G-11)"
    )


def test_the_one_environment_variable_the_job_reads_names_a_database() -> None:
    """The single channel that does exist, named so the claim above is exact.

    `forecast-fit` resolves its connection through `model.schema.url`, which
    reads `DATABASE_URL` — a variable that says *where* to run, never *what* to
    declare. Asserting its spelling is what keeps "no module reads the
    environment" from being a technicality about which package the read lives in.
    """
    assert DATABASE_URL_ENV_VAR == "DATABASE_URL"


# ---------------------------------------------------------------------------
# Door 3 — the constants themselves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("constant", PRE_REGISTERED_CONSTANTS)
def test_each_constant_is_assigned_once_in_the_package_from_a_literal(constant: str) -> None:
    """A committed value is a literal in one file, or it is not committed.

    Two properties, and the second is the one that matters: assigned in exactly
    one module — `config.py` — so there is no second opinion to drift, and
    assigned from a literal, so its value is a diff with an author rather than
    the result of something evaluated at import.
    """
    found = {module.name: assignments_to(parsed(module), constant) for module in package_modules()}
    sites = {name: values for name, values in found.items() if values}

    assert list(sites) == ["config.py"]
    assigned = sites["config.py"]
    assert len(assigned) == 1
    assert isinstance(assigned[0], ast.Constant), (
        f"`{constant}` is assigned from {ast.dump(assigned[0])[:60]}…; a computed value is "
        f"one a reader cannot date from the diff that introduced it"
    )


def test_the_declared_values_are_the_ones_ad_005_committed() -> None:
    """The values themselves, so a silent edit is a failing test and a diff.

    This is the assertion that turns "the constant is committed" into "the
    constant is *this*". A change here is deliberate and reviewable, which is
    the whole of what SC-032 asks for and the whole of what a check can offer.
    """
    assert HELD_OUT_FRACTION == 0.25
    assert SPLIT_SEED == 20260727
    assert config.split_seed_entropy() == str(SPLIT_SEED)


# ---------------------------------------------------------------------------
# Door 4 — the split's own interface
# ---------------------------------------------------------------------------


def test_the_split_accepts_no_parameter_that_could_override_either_value() -> None:
    """The last channel: an argument, which needs neither a flag nor a variable.

    `assign_split` takes the rows, the anchor and the input row hash and reads
    the two constants from `config.py` itself, so the split is a pure function
    of `(input_data_hash, SPLIT_SEED, HELD_OUT_FRACTION)` and a caller has
    nothing to vary.
    """
    parameters = tuple(inspect.signature(assign_split).parameters)

    assert parameters == ("lines", "as_of_date", "input_data_hash")
    assert split_module.HELD_OUT_FRACTION is config.HELD_OUT_FRACTION
    assert split_module.SPLIT_SEED is config.SPLIT_SEED


# ---------------------------------------------------------------------------
# G-11 — the half no check covers, demonstrated rather than claimed
# ---------------------------------------------------------------------------


def test_the_committed_fraction_is_authoritative_which_is_why_only_history_covers_it(
    fit_input: FitInput, emitted_run: EmittedRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G-11 made visible: the constant decides, and nothing records *when* it was set.

    The split is re-drawn over the same rows with the fraction rebound to a
    materially different value, and the realized proportion follows it. That is
    the correct behaviour — and it is precisely why SC-032's prohibition half is
    verified by reading the commit history of `config.py` and by no assertion in
    this suite. Every door above is closed; none of them can tell a fraction
    fixed before the result from one fixed after it.
    """
    lines = fit_input.procurement_input.lines
    committed = fit_input.split
    monkeypatch.setattr(split_module, "HELD_OUT_FRACTION", DEMONSTRATION_FRACTION)
    reshuffled = assign_split(lines, emitted_run.as_of_date, fit_input.input_data_hash)

    def held_out_share(result) -> float:
        assignments = result.assignments
        return sum(1 for one in assignments if one.split_side == HELD_OUT) / len(assignments)

    assert held_out_share(committed) == pytest.approx(HELD_OUT_FRACTION, abs=0.02)
    assert held_out_share(reshuffled) == pytest.approx(DEMONSTRATION_FRACTION, abs=0.02)
    assert committed.split_assignment_hash != reshuffled.split_assignment_hash, (
        "re-drawing the split at another fraction produced the same assignment hash, so the "
        "demonstration above is not showing that the constant is authoritative"
    )
