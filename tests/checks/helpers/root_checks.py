"""Reading what the repository-root check package itself imports.

TR-042. The root `tests/checks/` tree exists under a narrow exception: cross-entry
verification that no single entry owns. An entry-local test moved up here would
claim that exception without qualifying for it, and the giveaway is what the
module imports — a cross-entry check reads manifests, lockfiles, and built
images off the filesystem, while an entry-local test imports its entry's code or
the tooling that entry declares.

Kept in an importable helper rather than inline in a test body for the reason
the sibling helpers are: logic that hides inside a test function is measured as
covered the moment the test runs, which says nothing about the logic.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKS_ROOT = REPO_ROOT / "tests" / "checks"

# `tests/fixtures/` is deliberately excluded and is not scanned here. Those
# trees are input to other checks — they contain planted violations whose whole
# purpose is to be found — so reading them as if they were check code would
# report the fixtures as the defect they exist to detect.
EXCLUDED_DIRS = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache"})


def root_check_modules() -> list[Path]:
    """Every Python module under `tests/checks/`, helpers included."""
    return sorted(
        path
        for path in CHECKS_ROOT.rglob("*.py")
        if not any(part in EXCLUDED_DIRS for part in path.parts)
    )


def imported_root_packages(path: Path) -> set[str]:
    """Root package name of every absolute import in `path`.

    Parsed, not grepped, so a package named in a docstring or a failure message
    is not a finding — these checks name the entries constantly — while
    `import model.schema.url as _u` is.

    Relative imports are skipped: `from . import x` cannot reach outside this
    package, so attributing it to a top-level name would be wrong.
    """
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots
