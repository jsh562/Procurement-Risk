"""Applying the committed invented-name convention to roster entries.

TR-017. This lives in an importable module rather than inline in a test body
for the reason AD-002 gives: check logic inside a test function is measured as
covered the moment the test runs, which says nothing about the logic. Every
other check in this repository was extracted for that reason; this one was
overlooked until QC caught it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from model.roster.reader import DEFAULT_ROSTER_PATH

# Derived from the reader's own path rather than recomputed. Two independent
# `parents[n]` walks to the same directory is one typo away from a check that
# reads a file nobody is maintaining.
ROSTER_DIR = DEFAULT_ROSTER_PATH.parent
CONVENTION_PATH = ROSTER_DIR / "naming-convention.json"
EXCLUSIONS_PATH = ROSTER_DIR / "real-firm-exclusions.json"


@dataclass(frozen=True)
class Violation:
    kind: str
    identifier: str
    name: str
    reason: str


def load_convention() -> dict:
    return json.loads(CONVENTION_PATH.read_text(encoding="utf-8"))


def load_exclusions() -> list[str]:
    return json.loads(EXCLUSIONS_PATH.read_text(encoding="utf-8"))["excluded"]


def normalize(name: str, convention: dict | None = None) -> str:
    """Apply the committed normalization before any comparison.

    The rules are read from the convention file rather than hard-coded, so a
    change to normalization cannot silently disagree with the document that
    claims to define it.
    """
    rules = (convention or load_convention())["normalization"]
    text = name.casefold() if rules.get("casefold") else name
    for character in rules.get("strip_punctuation", []):
        text = text.replace(character, "")
    return " ".join(text.split()) if rules.get("collapse_whitespace") else text


def check_entries(entries, kind: str, convention: dict | None = None) -> list[Violation]:
    """Return every way ``entries`` departs from the convention.

    Returns all violations rather than the first, so one run names every
    offending entry — TR-019's reporting obligation applied to this check.
    """
    convention = convention or load_convention()
    excluded = {normalize(name, convention) for name in load_exclusions()}
    name_pattern = re.compile(convention[kind]["name_pattern"])
    id_pattern = re.compile(convention[kind]["identifier_pattern"])

    violations: list[Violation] = []
    for entry in entries:
        if not id_pattern.fullmatch(entry.id):
            violations.append(Violation(kind, entry.id, entry.name, "identifier scheme"))
        if not name_pattern.fullmatch(entry.name):
            violations.append(Violation(kind, entry.id, entry.name, "naming convention"))
        if normalize(entry.name, convention) in excluded:
            violations.append(Violation(kind, entry.id, entry.name, "matches a real firm"))
    return violations


def format_violations(violations: list[Violation]) -> str:
    return "\n".join(f"  {v.kind} {v.identifier} {v.name!r}: {v.reason}" for v in violations)
