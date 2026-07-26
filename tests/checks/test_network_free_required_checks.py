"""FR-008b / SC-009: no required check depends on the network.

The re-verification job re-fetches every recorded corpus source, so a workflow
that invoked it would make a required check depend on a third-party host being
up. That exclusion is the reason FR-008b states a cadence and an owner — the
repository administrator runs the job before each release tag and records its
outcome — instead of scheduling it, and the exclusion itself is the half of the
requirement a committed check *can* observe.

Lives here rather than in the modeling entry because the artifact under
assertion is a repository workflow, which no single entry owns — the same
exception `test_orchestration.py` and `test_layout.py` sit under.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# The console entry point and the module behind it. Both spellings are checked:
# a workflow could reach the job through `python -m` without ever naming the
# script, and a scan that only knew the script name would report clean.
NETWORK_DEPENDENT = ("corpus-reverify", "model.corpus.reverify", "corpus/reverify")


def _workflows() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.y*ml"))


def test_the_workflow_directory_is_not_empty() -> None:
    """Positive control: a scan over zero files passes by having nothing to fail."""
    assert _workflows(), f"no workflows found under {WORKFLOW_DIR}"


@pytest.mark.parametrize("name", NETWORK_DEPENDENT)
def test_no_workflow_invokes_the_re_verification_job(name: str) -> None:
    naming = [path.name for path in _workflows() if name in path.read_text(encoding="utf-8")]
    assert naming == [], (
        f"{naming} name {name!r}; the re-verification job re-fetches from the network "
        "and must not be reachable from a required check (FR-008b)"
    )


def test_the_scan_reports_a_planted_invocation(tmp_path: Path) -> None:
    """A check that cannot fail proves nothing."""
    planted = tmp_path / "verify.yml"
    planted.write_text("jobs:\n  x:\n    steps:\n      - run: corpus-reverify\n", encoding="utf-8")
    assert [
        path.name for path in [planted] if "corpus-reverify" in path.read_text(encoding="utf-8")
    ] == ["verify.yml"]
