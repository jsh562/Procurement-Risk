"""Source-level scan for provider-client mentions across all four entries.

TR-010. This is the check that catches what the import contract cannot: a
module obtaining the client by reading it off a permitted module leaves no
direct import edge, so `lint-imports` passes and only a textual scan objects.

The scanned file set is deliberately narrow. Scanning whole directories fails
on a clean tree — the gateway's manifest declares ``anthropic`` by name, and
all three lockfiles record it transitively through the path dependency both
boundaries declare. Restricting to source extensions is what makes "named in
exactly one file" true of a correct repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx"})

# Directories excluded wherever they appear beneath an entry. Installed
# packages carry the provider's own source, and a fixture is a deliberate
# violation whose whole purpose is to be found by a different check.
EXCLUDED_DIRS = frozenset(
    {".venv", "node_modules", "__pycache__", ".next", ".ruff_cache", ".pytest_cache"}
)


@dataclass(frozen=True)
class Mention:
    """One file naming the scanned symbol, with the first line that does."""

    path: Path
    line_number: int
    line: str


def _is_scannable(path: Path, fixture_root: Path | None) -> bool:
    if path.suffix not in SOURCE_SUFFIXES:
        return False
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return False
    if fixture_root is not None:
        try:
            path.relative_to(fixture_root)
        except ValueError:
            return True
        return False
    return True


def scannable_files(root: Path, fixture_root: Path | None = None) -> list[Path]:
    """Every file the scan below will actually read.

    Public because "named in exactly one file" is satisfied just as well by a
    scan that reads one file as by one that reads the whole tree, and the two
    are indistinguishable from the count alone. A caller that needs to know the
    denominator is real — that a newly added package is inside it rather than
    quietly excluded — asks here rather than reimplementing the filter and
    asserting against a second copy of the rule.
    """
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and _is_scannable(path, fixture_root)
    ]


def scan_source_root(root: Path, name: str, fixture_root: Path | None = None) -> list[Mention]:
    """Return every source file under ``root`` naming ``name`` as a whole word.

    Matching is case-sensitive and whole-word, and deliberately does not skip
    comments or strings: a name assembled at runtime is already invisible to
    the import contract, so narrowing this scan to real import statements
    would remove the only check that sees the evasion at all.
    """
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    mentions: list[Mention] = []
    for path in scannable_files(root, fixture_root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                mentions.append(Mention(path=path, line_number=number, line=line.strip()))
                break
    return mentions


def format_violation(name: str, mentions: list[Mention], repo_root: Path) -> str:
    """Render a failure message naming every offending file (TR-019)."""
    lines = [f"{name!r} is named in {len(mentions)} files; exactly 1 is permitted:"]
    lines.extend(
        f"  {m.path.relative_to(repo_root).as_posix()}:{m.line_number}: {m.line}" for m in mentions
    )
    return "\n".join(lines)
