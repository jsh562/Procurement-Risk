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
    # import-linter renders a banner containing non-ASCII box characters through
    # `rich`. Under a non-TTY stdout on Windows the child falls back to the
    # legacy cp1252 console encoder, and the parent's reader thread then dies
    # with UnicodeDecodeError — leaving `completed.stdout` as None and turning
    # every fixture assertion into `NoneType + str` rather than a verdict.
    #
    # `.github/workflows/verify.yml` already sets this for the same reason and
    # says so in a comment. Setting it there alone made the contract fixtures
    # pass on CI and fail locally on Windows, which is the worst arrangement:
    # the check that proves the other checks can fail was itself unreliable
    # exactly where a developer would run it. The helper now carries its own
    # requirement instead of inheriting it from whoever invoked it.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
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
        # Both halves are needed. `PYTHONUTF8` above makes the child *emit*
        # UTF-8; this makes the parent *decode* it. `text=True` alone would
        # decode with the parent's locale — cp1252 on Windows — so the child
        # writing correct UTF-8 would still kill the reader thread. `errors`
        # is set so a surprising byte degrades one character rather than
        # discarding the whole verdict.
        encoding="utf-8",
        errors="replace",
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
