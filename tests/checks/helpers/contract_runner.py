"""Running an import contract against a fixture tree and capturing its verdict.

TR-007. The negative fixtures cannot live inside a production contract root:
every contract runs over its whole root package, so a committed violation
placed there would break the real build rather than demonstrate that the
contract works. Each fixture therefore carries its own configuration and is
executed here in isolation.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures"
GATEWAY = REPO_ROOT / "src" / "gateway"


@dataclass(frozen=True)
class ContractResult:
    exit_code: int
    output: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    def names(self, text: str) -> bool:
        """Whether the output names something — TR-019's reporting obligation."""
        return text in self.output


def run_contract(fixture: str, config: str = ".importlinter") -> ContractResult:
    """Run ``lint-imports`` over one fixture, from that fixture's directory.

    The fixture directory goes on ``PYTHONPATH`` because import-linter builds
    its graph by importing the root package; a config alone is not enough for
    the package to be found.
    """
    fixture_dir = FIXTURE_ROOT / fixture
    env = dict(os.environ)
    env["PYTHONPATH"] = str(fixture_dir)
    env["UV_NATIVE_TLS"] = "1"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--directory",
            str(GATEWAY),
            "--no-sync",
            "lint-imports",
            "--config",
            str(fixture_dir / config),
        ],
        cwd=fixture_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return ContractResult(completed.returncode, completed.stdout + completed.stderr)


def python_version_matches_venv(interpreter: Path) -> bool:
    """Guard against a check silently running under the wrong interpreter.

    HINT-004: ``docker run --entrypoint python`` can resolve the system
    interpreter rather than the copied virtualenv, in which case an import
    failure proves nothing about what the image installed.
    """
    return interpreter.exists() and interpreter != Path(sys.executable)
