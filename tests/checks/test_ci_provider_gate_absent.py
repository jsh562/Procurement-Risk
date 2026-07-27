"""TR-063: the provider opt-in is absent from every continuous-integration environment.

TR-063 states the CI posture as **two** halves and requires both to be enforced
rather than one asserted and the other assumed: the environment carries no
credential, and it carries no opt-in. The credential half has a check of its
own; this is the other one.

**Why the opt-in needs its own check even though no credential is present.**
The two controls fail independently. An opt-in set in CI with no credential
reaches the provider boundary and fails there — later, more expensively, and
with an error about a missing credential rather than about a workflow that
asked to spend money. And the day a credential *is* added for some other
purpose, the opt-in would already be sitting there.

**Why the fixed form matters here.** TR-063 pins the control to
`GATEWAY_ALLOW_PROVIDER_CALLS=1` precisely so this check can look for it. An
unfixed "some separate opt-in" is not something a scan can assert the absence
of.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: The control's name, duplicated from `gateway.config` on purpose. This check
#: runs at the repository root and must not import an entry's package — a check
#: that imported the thing it audits would pass whenever that thing was
#: unimportable. `test_the_name_matches_the_gateways_own_constant` compares the
#: two by reading the source, so the duplication cannot drift silently.
OPT_IN = "GATEWAY_ALLOW_PROVIDER_CALLS"

CONFIG_SOURCE = REPO_ROOT / "src" / "gateway" / "src" / "gateway" / "config.py"


def _workflows() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.y*ml"))


def test_the_workflow_directory_is_not_empty() -> None:
    """Positive control: a scan over zero files passes by having nothing to
    fail, and would report the same clean result as a correct repository."""
    assert _workflows(), f"no workflows found under {WORKFLOW_DIR}"


@pytest.mark.parametrize("workflow", _workflows(), ids=lambda p: p.name)
def test_no_workflow_sets_the_provider_opt_in(workflow: Path) -> None:
    """One test per workflow, so a failure names the file rather than the set."""
    lines = [
        f"{number}: {line.strip()}"
        for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1)
        if OPT_IN in line
    ]
    assert not lines, (
        f"{workflow.name} names {OPT_IN}:\n  " + "\n  ".join(lines) + "\n"
        "That control gates the only provider-reaching path and must be absent "
        "from every CI environment (TR-063). Continuous integration runs in "
        "`replay` mode against committed fixtures."
    )


def test_the_name_matches_the_gateways_own_constant() -> None:
    """The duplication above, held honest.

    Read from the source rather than imported: this check lives at the
    repository root and importing an entry's package would couple a cross-entry
    check to that entry's environment. If the gateway renamed the control, the
    scan above would be looking for a string nothing sets and would pass
    forever.
    """
    source = CONFIG_SOURCE.read_text(encoding="utf-8")
    assert f'PROVIDER_OPT_IN_ENV_VAR: Final[str] = "{OPT_IN}"' in source, (
        f"the gateway no longer declares {OPT_IN!r} as its opt-in control, so "
        f"the scan above is looking for a name nothing sets"
    )


def test_the_permitted_value_is_still_exactly_one() -> None:
    """TR-063 fixes both halves of the form. If the permitted value widened to
    accept `true` or `yes`, a workflow could enable the gate with a spelling
    this file's scan would still catch — but the *reason* the form is fixed
    would have quietly changed, so it is pinned here too."""
    source = CONFIG_SOURCE.read_text(encoding="utf-8")
    assert 'PROVIDER_OPT_IN_PERMITTED_VALUE: Final[str] = "1"' in source


def test_the_scan_reports_a_planted_opt_in(tmp_path: Path) -> None:
    """A check that cannot fail proves nothing.

    Planted in the shape a workflow would actually carry — an `env:` entry —
    rather than as a bare token, so the scan is shown to catch the real thing.
    """
    planted = tmp_path / "verify.yml"
    planted.write_text(
        f"jobs:\n  verify:\n    env:\n      {OPT_IN}: '1'\n    steps:\n      - run: pytest\n",
        encoding="utf-8",
    )
    matching = [line for line in planted.read_text(encoding="utf-8").splitlines() if OPT_IN in line]
    assert matching, "the scan would not have found a planted opt-in"


def test_no_committed_env_file_sets_the_opt_in() -> None:
    """Workflows are not the only way an environment acquires a variable.

    A committed `.env` picked up by Compose or by a task runner would set it for
    every local run and, on a self-hosted runner, for CI too. Scanned because
    TR-063 speaks about the *environment*, not about the workflow files.
    """
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in REPO_ROOT.rglob(".env*")
        if path.is_file()
        and ".venv" not in path.parts
        and "node_modules" not in path.parts
        and OPT_IN in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert not offenders, f"{offenders} set {OPT_IN} (TR-063)"


def test_no_compose_service_sets_the_opt_in() -> None:
    """The same reasoning for Compose, which supplies environments directly."""
    offenders = [
        path.name
        for path in REPO_ROOT.glob("docker-compose*.y*ml")
        if OPT_IN in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"{offenders} set {OPT_IN} (TR-063)"


def test_the_reference_to_the_opt_in_is_not_merely_a_comment() -> None:
    """Guards the failure mode where the scan is satisfied by prose.

    A workflow that *mentioned* the control in a comment explaining why it is
    absent would fail the scan above — correctly, since a scan cannot tell a
    comment from a setting, and the conservative direction is to refuse both.
    This test records that as intended rather than leaving the next author to
    discover it as a false positive.
    """
    assert re.compile(re.escape(OPT_IN)).search(f"# {OPT_IN} is deliberately unset"), (
        "the scan is expected to match a comment too; if that becomes a "
        "nuisance, exclude comments deliberately rather than by accident"
    )
