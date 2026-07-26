"""TR-017 / TR-018 / VR-010 / VR-016: the roster's convention and disclosure."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from model.roster.naming import check_entries, format_violations, load_exclusions
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


@pytest.fixture(scope="module")
def roster():
    return read_roster()


# --- TR-017: the convention is applied by an importable check ----------------
# The logic lives in model.roster.naming so it lands in the coverage
# denominator. Inline in these bodies it was measured as covered the moment
# the test ran, which is the anti-pattern AD-002 exists to prevent.


def test_project_names_conform_to_the_committed_convention(roster) -> None:
    violations = check_entries(roster.projects, "projects")
    assert not violations, format_violations(violations)


def test_vendor_names_conform_to_the_committed_convention(roster) -> None:
    violations = check_entries(roster.vendors, "vendors")
    assert not violations, format_violations(violations)


def test_exclusion_list_is_sorted_and_unique() -> None:
    entries = load_exclusions()
    assert entries == sorted(entries), "exclusion list is not sorted"
    assert len(entries) == len(set(entries)), "exclusion list carries duplicates"


def test_the_convention_check_would_catch_a_real_firm() -> None:
    """Positive control: a check that never rejects anything proves nothing."""
    planted = [SimpleNamespace(id="VND-999", name="Turner Construction")]
    violations = check_entries(planted, "vendors")
    assert violations, "a real firm passed the naming convention"
    assert any(v.reason == "matches a real firm" for v in violations)


def test_the_convention_check_would_catch_a_bad_identifier() -> None:
    planted = [SimpleNamespace(id="BAD-1", name="Calvex Supply Co")]
    assert any(v.reason == "identifier scheme" for v in check_entries(planted, "vendors"))


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
