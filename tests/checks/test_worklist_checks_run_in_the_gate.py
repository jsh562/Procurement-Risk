"""FR-040: every check this feature adds executes in the merge gate.

"A committed test that no workflow step collects evidences nothing." That
sentence is why this file exists. E010 adds four tiers the workflow did not
previously run — the serving boundary's tests, its benchmark, the rendered-page
specs, and the fixture seeding those specs need — and each was a committed suite
with no execution site until it landed here.

The assertions are over the **parsed** workflow, never over the file's text. A
textual scan would pass on a step buried in a comment and fail on a comment
explaining why a step is absent, and `verify.yml` contains both kinds of prose.

The coverage half is asserted differently, and deliberately. Whether a floor
*fails* below its threshold cannot be checked by reading configuration — a
`--fail-under` in a command that no step runs enforces nothing. So the two
floors are checked for three things: that the threshold is declared, that a step
runs the command carrying it, and that this feature's source is inside the
measured set. A coverage figure that excludes this feature's code is a statement
about other epics.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"


@pytest.fixture(scope="module")
def steps() -> list[dict[str, Any]]:
    """The verify job's steps, parsed."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["verify"]["steps"]


def _named(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for step in steps:
        if step.get("name") == name:
            return step
    raise AssertionError(
        f"no step named {name!r}. FR-040: a committed check with no execution site "
        f"evidences nothing. Steps present: {[s.get('name') for s in steps if s.get('name')]}"
    )


@pytest.mark.parametrize(
    "name",
    [
        "Unit tests (api)",
        "Seed the frozen fixture",
        "E2E (web)",
        "Performance benchmark (api)",
    ],
)
def test_each_tier_this_feature_adds_has_an_execution_site(
    steps: list[dict[str, Any]], name: str
) -> None:
    """The four steps E010 needed. Before them, the serving boundary's tests and
    the rendered-page specs had no step at all — both were committed suites the
    gate never ran, which is the exact shape FR-040 names."""
    step = _named(steps, name)
    assert step.get("run"), f"{name} declares no command"


def test_the_api_tests_run_against_a_real_database(steps: list[dict[str, Any]]) -> None:
    """The eight degraded states are statements about what the server returns
    for arrays, generated columns and a partial unique index. A step without
    `DATABASE_URL` would skip every one of them and report green."""
    step = _named(steps, "Unit tests (api)")
    assert "DATABASE_URL" in step.get("env", {})


def test_the_unit_step_does_not_re_run_the_benchmark_tier(steps: list[dict[str, Any]]) -> None:
    """The benchmark has its own step under a CPU limit this one does not apply.
    Running it here too would take minutes and measure the wrong machine."""
    assert "not benchmark" in _named(steps, "Unit tests (api)")["run"]


def test_the_benchmark_runs_under_a_single_cpu(steps: list[dict[str, Any]]) -> None:
    """SC-017, SC-018. The registered envelope is p95 ≤ 1.5 s on **one shared
    vCPU**. A runner with more cores that passes a single-vCPU target has
    measured a machine nobody deploys on, so the limit is part of the check
    rather than part of the environment's good luck."""
    assert "taskset" in _named(steps, "Performance benchmark (api)")["run"]


def test_the_benchmark_publishes_its_figures(steps: list[dict[str, Any]]) -> None:
    """Principle VII. A miss is published with its figure rather than reported
    as a bare verdict — `-s` is what lets the printed p95 reach the log."""
    assert " -s" in _named(steps, "Performance benchmark (api)")["run"]


def test_the_end_to_end_specs_have_data_to_run_against(steps: list[dict[str, Any]]) -> None:
    """The specs drive a real page served by the real boundary, so the fixture
    has to be committed rather than seeded inside a rolled-back transaction — a
    separate server process sees only committed rows.

    Ordering matters and is asserted: seeding after the specs would leave them
    running against an empty database, which renders an honest empty state and
    would fail in a way that looks like a page defect.
    """
    names = [step.get("name") for step in steps]
    assert names.index("Seed the frozen fixture") < names.index("E2E (web)")
    assert "DATABASE_URL" in _named(steps, "Seed the frozen fixture").get("env", {})


def test_the_end_to_end_step_installs_a_browser(steps: list[dict[str, Any]]) -> None:
    """Playwright ships no browser with the package. Without this the step fails
    on a missing executable, which is a slow way to learn it."""
    assert "playwright install" in _named(steps, "E2E (web)")["run"]


class TestBothCoverageFloors:
    """FR-040. The 80% target applies on both sides of the request-serving
    boundary, and the two fail independently so neither can mask the other."""

    def test_the_python_floor_is_declared_and_enforced_by_a_step(
        self, steps: list[dict[str, Any]]
    ) -> None:
        commands = " ".join(step.get("run", "") for step in steps)
        assert "--fail-under=80" in commands, (
            "a threshold in configuration that no step enforces is a threshold nothing fails below"
        )

    def test_this_features_python_source_is_inside_the_measured_set(self) -> None:
        """A coverage figure that excludes this feature's code is a statement
        about other epics. `src/api` joined the list with E010, which is the
        first feature to put runtime code there."""
        manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        source = manifest["tool"]["coverage"]["run"]["source"]
        assert any("src/api" in entry for entry in source)

        # And the `paths` entry that goes with it: a package in `source` but
        # absent from `paths` lands in the denominator through the api step's
        # own data file with zero hits, dragging the combined figure down while
        # measuring nothing. E003's QC proved that failure mode; the two
        # settings are one change.
        paths = manifest["tool"]["coverage"]["paths"]
        assert any("api" in key for key in paths)

    def test_the_web_floor_is_declared_at_eighty_and_scoped_to_this_feature(self) -> None:
        """Vitest's own thresholds, scoped to `app/worklist/**`.

        Scoped rather than boundary-wide because the Next.js starter files still
        present here would dilute the denominator, and a floor that passes
        because of untouched scaffolding measures nothing.
        """
        config = (ROOT / "src" / "web" / "vitest.config.ts").read_text(encoding="utf-8")
        assert "thresholds" in config
        assert "app/worklist/**" in config
        for metric in ("lines", "branches", "functions", "statements"):
            assert f"{metric}: 80" in config

    def test_the_web_floor_is_enforced_by_the_step_that_runs(
        self, steps: list[dict[str, Any]]
    ) -> None:
        """`npm test` must be the variant that collects coverage — a step
        running `vitest run` without `--coverage` never evaluates a threshold,
        and the configuration above would sit there enforcing nothing."""
        manifest = __import__("json").loads(
            (ROOT / "src" / "web" / "package.json").read_text(encoding="utf-8")
        )
        assert "--coverage" in manifest["scripts"]["test"]
        assert "npm test" in _named(steps, "Unit tests (web)")["run"]

    def test_the_two_floors_are_separate_checks(self, steps: list[dict[str, Any]]) -> None:
        """Neither can mask the other: they are different tools, different
        steps, and different thresholds over disjoint source sets. A single
        combined figure would let a well-covered tier carry a bare one."""
        commands = {step.get("name"): step.get("run", "") for step in steps}
        python_floor = [name for name, run in commands.items() if "--fail-under=80" in run]
        web_floor = [name for name, run in commands.items() if "npm test" in run]

        assert python_floor and web_floor
        assert set(python_floor).isdisjoint(web_floor)
