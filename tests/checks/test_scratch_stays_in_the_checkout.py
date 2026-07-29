"""`project-instructions.md` v1.2.5 Temporary Files: scratch stays in `.tmp/`.

The rule has two halves and neither is self-enforcing. The pytest half is a
`--basetemp` in each manifest, and the process half is `TMPDIR`/`TEMP`/`TMP` on
each CI job — configuration in four `pyproject.toml` files and one workflow,
which is exactly the shape that drifts silently. It did: the root manifest was
the one Python tier without the pin while hosting
`test_gateway_no_provider_env.py`, the only pytest code in the repository that
builds a **virtual environment**, and `verify.yml`'s `verify` job set none of
the three variables while its `reproduce` job set all three. Both were found by
reading the files, which is the method this file replaces.

**Why the root manifest is included even though the clause says "each entry".**
v1.2.5 enumerates "`--basetemp` pinned there in each entry's pytest
configuration", and the root cross-entry harness is not one of the four entries
— which is how it was missed. The same clause's general obligation is that
*every* command directs scratch into the checkout's `.tmp/`, so the enumeration
under-specifies rather than exempts. This file asserts the general rule.

**Why it lives here.** It reads all four manifests and the workflow at once, so
no entry owns it — the narrow `/tests` exception `test_layout.py` and
`test_orchestration.py` sit under.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

from tests.checks.helpers.entries import PYTHON_ENTRIES, REPO_ROOT
from tests.checks.helpers.workflow import VERIFY_WORKFLOW, load_workflow

#: The directory the rule names. Gitignored, one per checkout.
SCRATCH = ".tmp"

#: The three variables the rule names, together. Setting two of three is worse
#: than setting none, because it looks configured — Python honours `TMPDIR`
#: first on POSIX and `TEMP` first on Windows, and a runner reusing these steps
#: on the other platform would fall back to the system directory silently.
PROCESS_VARIABLES = ("TMPDIR", "TEMP", "TMP")

#: What a job's value must resolve to. `github.workspace` is the checkout root,
#: which is what makes the path per-checkout rather than machine-wide.
EXPECTED_JOB_VALUE = "${{ github.workspace }}/.tmp"

#: Every Python tier that runs pytest: the four manifests, the root included.
MANIFESTS = (REPO_ROOT / "pyproject.toml",) + tuple(
    REPO_ROOT / "src" / entry / "pyproject.toml" for entry in PYTHON_ENTRIES
)


def _basetemp(manifest: Path) -> str | None:
    """The `--basetemp` a manifest's pytest configuration pins, if any."""
    options = (
        tomllib.loads(manifest.read_text(encoding="utf-8"))
        .get("tool", {})
        .get("pytest", {})
        .get("ini_options", {})
    )
    addopts = options.get("addopts")
    if addopts is None:
        return None
    words = addopts.split() if isinstance(addopts, str) else list(addopts)
    for word in words:
        if word.startswith("--basetemp="):
            return word.split("=", 1)[1]
    return None


def _inside_the_checkout(manifest: Path, basetemp: str) -> bool:
    """Whether the pinned path lands under this checkout.

    Resolved against the manifest's own directory, because that is the working
    directory every step running that tier uses — `uv run --directory src/model`
    is why the entries spell it `../../.tmp/pytest-model` and the root spells it
    `.tmp/pytest-checks`. Comparing the strings instead would accept
    `../../../elsewhere/.tmp` and reject a correct absolute path.
    """
    return _resolved(manifest, basetemp).is_relative_to(REPO_ROOT / SCRATCH)


def _resolved(manifest: Path, basetemp: str) -> Path:
    """The absolute directory a manifest's pin names, from that tier's cwd."""
    return (manifest.parent / basetemp).resolve()


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_every_pytest_tier_pins_its_basetemp_inside_the_checkout(manifest: Path) -> None:
    """The pytest half. Parametrized per manifest so a failure names the file."""
    basetemp = _basetemp(manifest)
    assert basetemp is not None, (
        f"{manifest.relative_to(REPO_ROOT)} runs pytest and pins no `--basetemp`, so "
        f"`tmp_path` and `tmp_path_factory` resolve through `tempfile.gettempdir()` — "
        f"the system temp directory, shared with the whole machine and with every other "
        f"checkout on this disk (project-instructions.md v1.2.5)"
    )
    assert _inside_the_checkout(manifest, basetemp), (
        f"{manifest.relative_to(REPO_ROOT)} pins --basetemp={basetemp!r}, which resolves "
        f"to {(manifest.parent / basetemp).resolve()} — outside {REPO_ROOT / SCRATCH}"
    )


def test_no_two_pytest_tiers_share_a_basetemp() -> None:
    """Inside the checkout is necessary and not sufficient: the directory must
    also be the tier's own.

    **pytest clears its basetemp at the start of every run.** All four manifests
    once named `.tmp/pytest`, which is correct in CI — the four tiers are
    sequential steps there, so each clears a directory the previous one is done
    with — and destructive anywhere they overlap. Two tiers started concurrently
    on a developer's machine delete each other's `tmp_path` trees mid-run, and
    the symptom is an unrelated test failing on a file that existed a moment
    earlier, in the tier that did *not* do anything wrong. That happened twice
    while this epic was being worked on, which is why the property is asserted
    rather than left to whoever adds the fifth manifest.
    """
    pinned = {manifest: _basetemp(manifest) for manifest in MANIFESTS}
    resolved: dict[Path, list[Path]] = {}
    for manifest, basetemp in pinned.items():
        assert basetemp is not None, manifest  # the test above owns this failure
        resolved.setdefault(_resolved(manifest, basetemp), []).append(manifest)

    shared = {
        str(directory): sorted(str(m.relative_to(REPO_ROOT)) for m in manifests)
        for directory, manifests in resolved.items()
        if len(manifests) > 1
    }
    assert not shared, (
        f"{shared} — pytest clears its basetemp at start of run, so two tiers pointed at "
        f"one directory wipe each other's `tmp_path` whenever they run concurrently. Give "
        f"each tier its own subdirectory of {REPO_ROOT / SCRATCH}"
    )


def test_the_basetemp_check_reports_a_manifest_that_pins_nothing(tmp_path: Path) -> None:
    """A check that cannot fail proves nothing, and this one would pass on any
    manifest with no `[tool.pytest.ini_options]` at all if it read the table
    rather than the option."""
    planted = tmp_path / "pyproject.toml"
    planted.write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\naddopts = "-q"\n', encoding="utf-8"
    )
    assert _basetemp(planted) is None

    planted.write_text(
        '[tool.pytest.ini_options]\naddopts = "--basetemp=/somewhere/else"\n', encoding="utf-8"
    )
    escaping = _basetemp(planted)
    assert escaping is not None
    assert not _inside_the_checkout(REPO_ROOT / "pyproject.toml", escaping)


def _job_environments() -> dict[str, dict[str, str]]:
    """Each job's effective environment: the workflow's, overlaid by the job's."""
    document = load_workflow()
    workflow_level = document.get("env") or {}
    return {
        name: {**workflow_level, **(job.get("env") or {})}
        for name, job in (document.get("jobs") or {}).items()
    }


def test_the_workflow_declares_at_least_two_jobs() -> None:
    """Positive control. The asymmetry this file exists for was *between* two
    jobs, so a per-job assertion over a single job describes nothing."""
    jobs = _job_environments()
    assert len(jobs) >= 2, f"{VERIFY_WORKFLOW} declares {sorted(jobs)}"


def test_every_workflow_job_directs_its_scratch_into_the_checkout() -> None:
    """The process half, over every job rather than the ones anyone remembered.

    Asserted as a property of the job set rather than of a named job: the
    finding was that one job of two had it, and a check naming `verify` would
    have to be edited by whoever adds the third job — which is the person least
    likely to know the rule exists.
    """
    offenders = {
        name: sorted(set(PROCESS_VARIABLES) - set(environment))
        for name, environment in _job_environments().items()
        if set(PROCESS_VARIABLES) - set(environment)
    }
    assert not offenders, (
        f"{offenders} — every job must set all three of {PROCESS_VARIABLES}, so that "
        f"`uv`, `npm`, `docker build` and every `tempfile` call inside it write into the "
        f"checkout's own `.tmp/` (project-instructions.md v1.2.5). Two of three is worse "
        f"than none: it reads as configured while one platform's lookup order still "
        f"finds the system directory"
    )

    wrong = {
        f"{name}.{variable}": environment[variable]
        for name, environment in _job_environments().items()
        for variable in PROCESS_VARIABLES
        if environment[variable] != EXPECTED_JOB_VALUE
    }
    assert not wrong, f"{wrong} do not resolve to the checkout root: {EXPECTED_JOB_VALUE!r}"


def test_the_job_environment_check_reports_a_job_that_sets_nothing(tmp_path: Path) -> None:
    """The negative control for the half above, planted as a whole workflow so
    the overlay of workflow-level `env` onto job-level `env` is exercised too —
    a check reading only the job's own mapping would report a false violation
    for a workflow that set the three globally."""
    planted = tmp_path / "verify.yml"
    planted.write_text(
        "env:\n  PYTHONUTF8: '1'\n"
        "jobs:\n"
        "  covered:\n"
        "    env:\n"
        "      TMPDIR: x\n      TEMP: x\n      TMP: x\n"
        "  bare:\n"
        "    runs-on: ubuntu-latest\n",
        encoding="utf-8",
    )
    document = yaml.safe_load(planted.read_text(encoding="utf-8"))
    workflow_level = document.get("env") or {}
    environments = {
        name: {**workflow_level, **(job.get("env") or {})} for name, job in document["jobs"].items()
    }
    missing = {
        name: sorted(set(PROCESS_VARIABLES) - set(environment))
        for name, environment in environments.items()
        if set(PROCESS_VARIABLES) - set(environment)
    }
    assert missing == {"bare": ["TEMP", "TMP", "TMPDIR"]}, missing
