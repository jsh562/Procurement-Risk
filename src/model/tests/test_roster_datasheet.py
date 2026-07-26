"""TR-017 / TR-018 / VR-010 / VR-016: the roster's convention and disclosure."""

from __future__ import annotations

import json
import re

import pytest

from model.roster.reader import DEFAULT_ROSTER_PATH, read_roster

ROSTER_DIR = DEFAULT_ROSTER_PATH.parent
DATASHEET = ROSTER_DIR / "roster-datasheet.md"
CONVENTION = json.loads((ROSTER_DIR / "naming-convention.json").read_text(encoding="utf-8"))
EXCLUSIONS = json.loads((ROSTER_DIR / "real-firm-exclusions.json").read_text(encoding="utf-8"))

REQUIRED_SECTIONS = (
    "Motivation and Composition",
    "Generation Process",
    "Uses and Distribution",
    "Maintenance and Out-of-Scope Content",
)


def _normalize(name: str) -> str:
    rules = CONVENTION["normalization"]
    text = name.casefold() if rules["casefold"] else name
    for character in rules["strip_punctuation"]:
        text = text.replace(character, "")
    return " ".join(text.split()) if rules["collapse_whitespace"] else text


# --- TR-017: the convention is applied by a check, not by review -------------


@pytest.fixture(scope="module")
def roster():
    return read_roster()


def test_project_names_conform_to_the_committed_pattern(roster) -> None:
    pattern = re.compile(CONVENTION["projects"]["name_pattern"])
    offenders = [p.name for p in roster.projects if not pattern.fullmatch(p.name)]
    assert not offenders, f"project names violate the naming convention: {offenders}"


def test_vendor_names_conform_to_the_committed_pattern(roster) -> None:
    pattern = re.compile(CONVENTION["vendors"]["name_pattern"])
    offenders = [v.name for v in roster.vendors if not pattern.fullmatch(v.name)]
    assert not offenders, f"vendor names violate the naming convention: {offenders}"


def test_no_roster_name_matches_the_real_firm_exclusion_list(roster) -> None:
    excluded = {_normalize(name) for name in EXCLUSIONS["excluded"]}
    hits = [e.name for e in (*roster.projects, *roster.vendors) if _normalize(e.name) in excluded]
    assert not hits, f"roster names match excluded real firms: {hits}"


def test_exclusion_list_is_sorted_and_unique() -> None:
    entries = EXCLUSIONS["excluded"]
    assert entries == sorted(entries), "exclusion list is not sorted"
    assert len(entries) == len(set(entries)), "exclusion list carries duplicates"


def test_the_convention_check_would_catch_a_bad_name() -> None:
    """Positive control: a pattern that matches everything proves nothing."""
    pattern = re.compile(CONVENTION["vendors"]["name_pattern"])
    assert not pattern.fullmatch("Turner Construction")


# --- TR-018 / VR-010 / VR-016: the datasheet ---------------------------------


@pytest.mark.parametrize("heading", REQUIRED_SECTIONS)
def test_datasheet_carries_every_required_section(heading: str) -> None:
    text = DATASHEET.read_text(encoding="utf-8")
    assert re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.M)


def test_datasheet_declares_synthetic_status_with_the_literal_token() -> None:
    assert "SYNTHETIC" in DATASHEET.read_text(encoding="utf-8")


def test_datasheet_states_both_population_sizes(roster) -> None:
    text = DATASHEET.read_text(encoding="utf-8")
    assert str(len(roster.projects)) in text and str(len(roster.vendors)) in text


def test_datasheet_reproduces_no_literal_digest() -> None:
    """VR-016. A digest copied into prose is a second source of truth that
    nothing updates; a stale one is indistinguishable from real drift."""
    text = DATASHEET.read_text(encoding="utf-8")
    assert not re.search(r"\b[0-9a-f]{64}\b", text), "datasheet contains a literal digest"
    assert "sha256:" not in text
