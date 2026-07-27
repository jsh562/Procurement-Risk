"""T081–T083 — the package is measured, declares nothing new, and is gated.

Each of these is about the *build* rather than the dataset, and each fails
silently if unasserted: a package missing from `--source` lands in the coverage
denominator with zero hits, a new dependency slips in unremarked, and a release
gate that names no command is a step that passes by doing nothing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from model.procurement import paths

WORKFLOW = paths.REPO_ROOT / ".github" / "workflows" / "verify.yml"
MODEL_PYPROJECT = paths.REPO_ROOT / "src" / "model" / "pyproject.toml"
ROOT_PYPROJECT = paths.REPO_ROOT / "pyproject.toml"


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def steps(workflow_text: str) -> list[dict]:
    """The workflow's steps, parsed without a YAML dependency.

    PyYAML is not a declared dependency of this entry and adding one to read a
    CI file would be a new third-party import for a test that exists to assert
    no new third-party imports. The structure needed here is shallow — step name,
    working directory, and the `run:` block — so it is scanned directly.
    """
    steps: list[dict] = []
    current: dict | None = None
    collecting = False
    for line in workflow_text.splitlines():
        if line.startswith("      - name: "):
            if current:
                steps.append(current)
            current = {"name": line.split("- name: ", 1)[1].strip(), "run": ""}
            collecting = False
        elif current is not None and line.startswith("        working-directory: "):
            current["working-directory"] = line.split(": ", 1)[1].strip()
        elif current is not None and line.strip().startswith("run:"):
            collecting = True
            inline = line.split("run:", 1)[1].strip()
            if inline and inline != "|":
                current["run"] += inline + "\n"
                collecting = False
        elif collecting and current is not None:
            if line.startswith("          ") or not line.strip():
                current["run"] += line.strip() + "\n"
            else:
                collecting = False
    if current:
        steps.append(current)
    return steps


class TestCoveragePlacement:
    """T081. `--source` **overrides** the root config rather than merging, so a
    package absent here is measured at zero however the root file lists it."""

    def test_the_procurement_package_is_in_the_source_list(self, steps) -> None:
        command = next(
            step["run"] for step in steps if "coverage run --source" in step.get("run", "")
        )
        source = command.split("--source=")[1].split()[0]
        assert "src/model/procurement" in source.split(",")

    def test_every_delivered_package_is_in_the_source_list(self, steps) -> None:
        """Each epic widened this line independently; the union is what is
        correct, and dropping any one measures nothing while dragging the gate."""
        command = next(
            step["run"] for step in steps if "coverage run --source" in step.get("run", "")
        )
        source = set(command.split("--source=")[1].split()[0].split(","))
        assert source >= {
            "src/model/roster",
            "src/model/schema",
            "src/model/corpus",
            "src/model/procurement",
        }

    def test_the_root_config_also_lists_the_package(self) -> None:
        config = tomllib.loads(ROOT_PYPROJECT.read_text(encoding="utf-8"))
        source = config["tool"]["coverage"]["run"]["source"]
        assert any("procurement" in entry for entry in source)


class TestNoNewDependency:
    """T082. The package is stdlib plus NumPy plus what the entry already had."""

    def test_the_model_entry_declares_no_new_runtime_dependency(self) -> None:
        config = tomllib.loads(MODEL_PYPROJECT.read_text(encoding="utf-8"))
        declared = {
            name.split(">")[0].split("[")[0].split("=")[0].strip()
            for name in config["project"]["dependencies"]
        }
        # Everything `model.procurement` imports from outside the stdlib.
        assert {"numpy", "sqlalchemy", "psycopg"} <= declared

    def test_the_package_imports_nothing_undeclared(self) -> None:
        """A scan of the package's own imports against the declared set, so a
        new third-party import fails here rather than at someone else's install."""
        config = tomllib.loads(MODEL_PYPROJECT.read_text(encoding="utf-8"))
        declared = {
            name.split(">")[0].split("[")[0].split("=")[0].strip().replace("-", "_")
            for name in config["project"]["dependencies"]
        }
        allowed = declared | {"model", "gateway"}

        # Parsed with `ast`, not scanned as text. A line-based scan reads prose
        # as code: a docstring beginning "from the fitting entry point's
        # configuration" contributed a dependency called `of`.
        import ast

        package = Path(paths.__file__).parent
        third_party: set[str] = set()
        for source in package.glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    third_party.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    third_party.add(node.module.split(".")[0])

        import sys

        undeclared = {
            name
            for name in third_party
            if name not in allowed and name not in sys.stdlib_module_names
        }
        assert not undeclared, f"undeclared imports: {sorted(undeclared)}"

    def test_the_three_console_entries_are_declared(self) -> None:
        config = tomllib.loads(MODEL_PYPROJECT.read_text(encoding="utf-8"))
        scripts = config["project"]["scripts"]
        assert scripts["procurement-generate"] == "model.procurement.generate:main"
        assert scripts["procurement-load"] == "model.procurement.load:main"
        assert scripts["procurement-validate"] == "model.procurement.validate:main"


class TestReleaseGate:
    """T083. The gate names commands, and each one is a checkable claim."""

    def test_the_release_gate_step_exists(self, steps) -> None:
        names = [step.get("name") for step in steps]
        assert "Procurement dataset release gate" in names

    def test_it_runs_the_validator(self, steps) -> None:
        gate = next(s for s in steps if s.get("name") == "Procurement dataset release gate")
        assert "procurement-validate" in gate["run"]

    @pytest.mark.parametrize(
        "check",
        [
            "test_ground_truth_isolation.py",
            "test_emitted_artifact_set.py",
            "test_gate_discharged.py",
            "test_corpus_match_fields.py",
        ],
    )
    def test_each_named_check_is_gated(self, steps, check: str) -> None:
        gate = next(s for s in steps if s.get("name") == "Procurement dataset release gate")
        assert check in gate["run"]

    def test_the_gate_runs_from_the_model_entry(self, steps) -> None:
        gate = next(s for s in steps if s.get("name") == "Procurement dataset release gate")
        assert gate["working-directory"] == "src/model"

    def test_the_orchestration_check_is_not_in_any_gate(self, steps) -> None:
        """Its teardown destroys the database volume. It must never be reachable
        from a step that runs automatically."""
        for step in steps:
            assert "test_orchestration" not in step.get("run", "")
