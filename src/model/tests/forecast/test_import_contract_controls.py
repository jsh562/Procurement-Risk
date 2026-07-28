"""T108 — NC-15 / FR-024 / DV-022: the forecast contract's failing direction.

`src/model/pyproject.toml` declares *Forecast code does not reach the model
provider* — `model.forecast` may reach neither `model.llm` nor `gateway`, with
`allow_indirect_imports = false`. It passes on this tree, and a contract naming a
module that behaves is green whether the contract works or not. NC-15 asks for
**two** plantings, because the two violations it must catch fail for different
reasons and one implementation setting would silence one of them:

* a **direct** import of `gateway` from a module under the source package;
* an **indirect** one, laundered through an ordinary intermediate module. That is
  the edge `allow_indirect_imports = false` exists for, and with indirect
  detection off it is invisible — the fit imports a relay, and only the relay
  names the provider client.

The plantings target `gateway` specifically. The committed `forecast_offline`
fixture at the repository root stands in for this contract in the cross-entry
harness and plants `anthropic` as its external target; `gateway` is the other
forbidden module the real contract names, and reaching the provider through the
shared client is the same violation by another route.

**What this does not close** is recorded rather than implied: G-17 bounds a
static contract to what an import edge predicts. A provider reached without one —
a dynamically constructed import, or a raw HTTP call — is outside it, and the
residual is bounded by `src/model` declaring neither a provider client nor a
general-purpose HTTP client rather than by this file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

#: The contract as `src/model/pyproject.toml` declares it — the name, the source
#: package and both forbidden modules. Written out here and reconciled against
#: the parsed manifest below, rather than read out of it: a planting derived from
#: the manifest would follow a narrowed contract wherever it went and keep
#: reporting green.
CONTRACT_NAME = "Forecast code does not reach the model provider"
SOURCE_PACKAGE = "model.forecast"
FORBIDDEN_MODULES = ("model.llm", "gateway")

#: The planted package's own name. Deliberately not `model`: every contract runs
#: over its whole root package, so a violation committed inside the real one
#: would break the build rather than demonstrate that the contract works.
FIXTURE_PACKAGE = "forecast_gateway_control"

#: The two offending modules, by the name the failure output must carry.
DIRECT_MODULE = "direct_reach"
INDIRECT_MODULE = "laundered_reach"
RELAY_MODULE = "relay"

#: How long `lint-imports` is given. It builds a graph by importing the package
#: and its externals, which is seconds; a hung child would otherwise leave the
#: tier waiting with no output explaining why.
CONTRACT_TIMEOUT_SECONDS = 300.0

#: `src/model/pyproject.toml`, read to reconcile the plantings against the
#: contract they stand in for.
MODEL_MANIFEST = Path(__file__).resolve().parents[2] / "pyproject.toml"


@dataclass(frozen=True, slots=True)
class ContractResult:
    """One `lint-imports` run: its status and everything it wrote.

    Both streams are joined because import-linter renders its verdict through
    `rich`, which chooses a stream by whether the console is a TTY — and a check
    that read only one of them would be reliable in exactly one environment.
    """

    exit_code: int
    output: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    def names(self, text: str) -> bool:
        """Whether the output names something — the reporting obligation itself."""
        return text in self.output


def write_fixture_tree(root: Path, *, indirect: bool) -> Path:
    """A one-package tree that violates the contract in exactly one way.

    One violation per tree rather than both in one, so a failing run attributes
    the breach to the planting under test: a tree carrying both would fail on
    either, and neither assertion would tell a reader which detection setting was
    doing the work.
    """
    package = root / FIXTURE_PACKAGE
    (package / "forecast").mkdir(parents=True)
    (package / "__init__.py").write_text(
        '"""Stands in for the modeling entry\'s root package."""\n', encoding="utf-8"
    )
    (package / "forecast" / "__init__.py").write_text(
        '"""Stands in for `model.forecast`: the contract\'s source module."""\n',
        encoding="utf-8",
    )
    if indirect:
        (package / f"{RELAY_MODULE}.py").write_text(
            '"""An ordinary intermediate module that happens to import the client."""\n'
            "import gateway\n\n"
            '__all__ = ["gateway"]\n',
            encoding="utf-8",
        )
        (package / "forecast" / f"{INDIRECT_MODULE}.py").write_text(
            '"""Violation: the reach is laundered through an ordinary module."""\n'
            f"from {FIXTURE_PACKAGE} import {RELAY_MODULE}\n\n"
            f'__all__ = ["{RELAY_MODULE}"]\n',
            encoding="utf-8",
        )
    else:
        (package / "forecast" / f"{DIRECT_MODULE}.py").write_text(
            '"""Violation: the reach is a direct import of the shared client."""\n'
            "import gateway\n\n"
            '__all__ = ["gateway"]\n',
            encoding="utf-8",
        )

    configuration = root / ".importlinter"
    configuration.write_text(
        "[importlinter]\n"
        f"root_package = {FIXTURE_PACKAGE}\n"
        "# `gateway` sits outside the root package, and import-linter refuses to\n"
        "# name an external module as forbidden unless the graph carries external\n"
        "# packages — the same setting the real contract needs, or this fixture\n"
        "# would demonstrate a contract shaped differently from the one it stands\n"
        "# in for.\n"
        "include_external_packages = True\n"
        "\n"
        "[importlinter:contract:forecast-gateway]\n"
        f"name = {CONTRACT_NAME}\n"
        "type = forbidden\n"
        "source_modules =\n"
        f"    {FIXTURE_PACKAGE}.forecast\n"
        "forbidden_modules =\n"
        "    gateway\n"
        "allow_indirect_imports = False\n",
        encoding="utf-8",
    )
    return configuration


def lint_imports_executable() -> Path:
    """This environment's `lint-imports`, beside the interpreter running the tests.

    Resolved beside `sys.executable` first for the reason
    `conftest.console_script_path` resolves `forecast-fit` that way: the contract
    must be run by the entry whose graph it is a claim about, and `PATH` may name
    another environment's.
    """
    suffix = ".exe" if os.name == "nt" else ""
    candidate = Path(sys.executable).parent / f"lint-imports{suffix}"
    if candidate.exists():
        return candidate
    located = shutil.which("lint-imports")
    if located is None:
        raise pytest.UsageError(
            "`lint-imports` is not installed in this environment, so the forecast import "
            "contract's failing direction cannot be demonstrated. Install the entry's dev "
            "group with `uv sync --directory src/model`."
        )
    return Path(located)


def run_contract(configuration: Path) -> ContractResult:
    """Run the contract over one planted tree and capture its verdict.

    The fixture directory goes on `PYTHONPATH` because import-linter builds its
    graph by importing the root package; a configuration file alone would leave
    the package unfindable and the run would fail for a reason that is not the
    contract. UTF-8 is forced on both sides for the reason the root harness
    records: `rich` emits box characters, and a Windows console falling back to
    cp1252 kills the parent's reader thread and turns a verdict into `None`.
    """
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(configuration.parent)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(  # noqa: S603 - fixed argv from this module's constants
        [str(lint_imports_executable()), "--config", str(configuration)],
        cwd=configuration.parent,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=CONTRACT_TIMEOUT_SECONDS,
    )
    return ContractResult(completed.returncode, completed.stdout + completed.stderr)


@pytest.fixture(scope="module")
def direct_reach(tmp_path_factory) -> ContractResult:
    """NC-15's first planting, run once."""
    return run_contract(write_fixture_tree(tmp_path_factory.mktemp("direct"), indirect=False))


@pytest.fixture(scope="module")
def indirect_reach(tmp_path_factory) -> ContractResult:
    """NC-15's second planting, run once."""
    return run_contract(write_fixture_tree(tmp_path_factory.mktemp("indirect"), indirect=True))


def test_the_real_contract_still_names_gateway_as_forbidden() -> None:
    """The reconciliation: these plantings stand in for the contract as declared.

    A contract narrowed in the manifest — `gateway` dropped from the forbidden
    list, or indirect detection turned off — would leave both plantings below
    passing while asserting nothing about the rule the repository actually has.

    Parsed rather than searched as text, so a contract that merely *mentions*
    `gateway` in a comment above a forbidden list that no longer names it fails
    here, which is the shape a quiet narrowing would take.
    """
    manifest = tomllib.loads(MODEL_MANIFEST.read_text(encoding="utf-8"))
    contracts = manifest["tool"]["importlinter"]["contracts"]
    declared = next(
        (contract for contract in contracts if contract["name"] == CONTRACT_NAME), None
    )

    assert declared is not None, (
        f"`src/model/pyproject.toml` declares {[c['name'] for c in contracts]} and not "
        f"{CONTRACT_NAME!r}; these plantings stand in for a contract the entry no longer has"
    )
    assert declared["type"] == "forbidden"
    assert declared["source_modules"] == [SOURCE_PACKAGE]
    assert tuple(declared["forbidden_modules"]) == FORBIDDEN_MODULES
    assert declared["allow_indirect_imports"] is False, (
        "indirect detection is off on the real contract, so the laundered reach planted "
        "below is a violation the repository would not catch"
    )
    assert manifest["tool"]["importlinter"]["include_external_packages"] is True, (
        "`gateway` sits outside the root package; without external packages in the graph "
        "the contract does not merely weaken — it fails to load at all"
    )


def test_a_direct_import_of_the_gateway_fails_the_contract(
    direct_reach: ContractResult,
) -> None:
    """NC-15's first direction: reaching the shared client with nothing in between.

    The offending module is required to be **named**, not merely counted: a
    non-zero exit tells a reviewer that something broke, and naming the module is
    what tells them where.
    """
    assert not direct_reach.passed, (
        f"a direct `import gateway` from the source package did not break the "
        f"contract:\n{direct_reach.output}"
    )
    assert direct_reach.names(DIRECT_MODULE), (
        f"the failure did not name the offending module:\n{direct_reach.output}"
    )
    assert direct_reach.names(CONTRACT_NAME), (
        f"the failure did not name the violated contract:\n{direct_reach.output}"
    )


def test_an_indirect_import_of_the_gateway_fails_the_contract(
    indirect_reach: ContractResult,
) -> None:
    """NC-15's second direction, and the one `allow_indirect_imports` decides.

    Both ends of the laundered chain are required in the output — the module that
    reached and the relay it reached through — because an operator holding only
    one of them cannot see the edge that was actually taken.
    """
    assert not indirect_reach.passed, (
        f"a reach laundered through an ordinary relay did not break the contract, which "
        f"is what `allow_indirect_imports = false` exists to catch:\n{indirect_reach.output}"
    )
    assert indirect_reach.names(INDIRECT_MODULE), (
        f"the failure did not name the offending module:\n{indirect_reach.output}"
    )
    assert indirect_reach.names(RELAY_MODULE), (
        f"the failure did not name the laundering module, so the chain the contract "
        f"detected is invisible to a reader:\n{indirect_reach.output}"
    )


def test_the_two_plantings_fail_for_different_reasons(
    direct_reach: ContractResult, indirect_reach: ContractResult
) -> None:
    """The reason NC-15 counts two rather than one.

    Neither output names the other's offender, which is what shows the two trees
    are separate plantings rather than one violation observed twice — and it is
    what would surface if a later edit collapsed them into a single fixture.
    """
    assert not direct_reach.names(INDIRECT_MODULE)
    assert not direct_reach.names(RELAY_MODULE)
    assert not indirect_reach.names(DIRECT_MODULE)


def test_a_tree_with_no_reach_passes_the_same_contract(tmp_path: Path) -> None:
    """The positive control, without which both plantings prove only that it fails.

    The same contract, the same package shape, no import of `gateway` anywhere:
    it passes. A contract that failed on every tree would satisfy every assertion
    above and would be worthless as a build gate.
    """
    root = tmp_path / "clean"
    package = root / FIXTURE_PACKAGE
    (package / "forecast").mkdir(parents=True)
    (package / "__init__.py").write_text('"""Clean root package."""\n', encoding="utf-8")
    (package / "forecast" / "__init__.py").write_text(
        '"""Clean source module: it reaches the standard library and nothing else."""\n'
        "import math\n\n"
        '__all__ = ["math"]\n',
        encoding="utf-8",
    )
    configuration = write_fixture_tree(tmp_path / "unused", indirect=False)
    (root / ".importlinter").write_text(
        configuration.read_text(encoding="utf-8"), encoding="utf-8"
    )

    result = run_contract(root / ".importlinter")

    assert result.passed, (
        f"the contract failed on a tree that reaches nothing forbidden, so its failures "
        f"say nothing about the reaches it is supposed to catch:\n{result.output}"
    )
