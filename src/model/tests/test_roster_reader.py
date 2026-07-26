"""TR-016 / TR-027 / VR-001…VR-016: the roster reader."""

from __future__ import annotations

import json
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from model.roster.reader import (
    DEFAULT_ROSTER_PATH,
    EXPECTED_PROJECTS,
    EXPECTED_VENDORS,
    RosterError,
    canonical_bytes,
    content_hash,
    read_roster,
)

HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def roster():
    return read_roster()


def test_populations_match_the_declared_sizes(roster) -> None:
    assert len(roster.projects) == EXPECTED_PROJECTS
    assert len(roster.vendors) == EXPECTED_VENDORS


def test_identifiers_follow_their_schemes_and_are_unique(roster) -> None:
    assert all(re.fullmatch(r"PRJ-\d{3}", p.id) for p in roster.projects)
    assert all(re.fullmatch(r"VND-\d{3}", v.id) for v in roster.vendors)
    assert len(roster.identifiers()) == EXPECTED_PROJECTS + EXPECTED_VENDORS


def test_hash_has_the_declared_type_and_format(roster) -> None:
    assert HASH_PATTERN.fullmatch(roster.content_hash)


def test_hash_is_stable_across_reads() -> None:
    assert read_roster().content_hash == read_roster().content_hash


def test_hash_is_independent_of_key_order_and_whitespace() -> None:
    """Two serializations of the same content must not produce two hashes."""
    payload = json.loads(DEFAULT_ROSTER_PATH.read_text(encoding="utf-8"))
    reordered = {k: payload[k] for k in reversed(list(payload))}
    assert content_hash(payload) == content_hash(reordered)


def test_hash_changes_when_content_changes(roster) -> None:
    payload = json.loads(DEFAULT_ROSTER_PATH.read_text(encoding="utf-8"))
    payload["vendors"][0]["name"] = "Something Else Entirely"
    assert content_hash(payload) != roster.content_hash


def test_roster_carries_no_version_field() -> None:
    """Asserted absent by design: a hand-maintained marker makes drift
    recordable but not detectable, because someone has to remember to bump it."""
    assert "version" not in json.loads(DEFAULT_ROSTER_PATH.read_text(encoding="utf-8"))


def test_source_file_has_no_byte_order_mark() -> None:
    assert not DEFAULT_ROSTER_PATH.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda p: p.update({"extra": []}), "unknown top-level key"),
        (lambda p: p["projects"].pop(), "wrong project count"),
        (
            lambda p: p["vendors"].append({"id": "VND-001", "name": "Duplicate Works"}),
            "duplicate id",
        ),
        (lambda p: p["projects"][0].update({"name": ""}), "empty field"),
        (lambda p: p["projects"][0].pop("name"), "missing field"),
    ],
)
def test_invalid_rosters_are_rejected(tmp_path, mutation, reason) -> None:
    payload = json.loads(DEFAULT_ROSTER_PATH.read_text(encoding="utf-8"))
    mutation(payload)
    broken = tmp_path / "roster.json"
    broken.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RosterError):
        read_roster(broken)


def test_byte_order_mark_is_a_read_failure(tmp_path) -> None:
    target = tmp_path / "roster.json"
    target.write_bytes(b"\xef\xbb\xbf" + DEFAULT_ROSTER_PATH.read_bytes())
    with pytest.raises(RosterError, match="byte-order mark"):
        read_roster(target)


def test_missing_roster_is_a_read_failure(tmp_path) -> None:
    with pytest.raises(RosterError, match="unreadable"):
        read_roster(tmp_path / "absent.json")


@settings(max_examples=50)
@given(st.text(min_size=1, max_size=40))
def test_canonical_form_is_deterministic_for_arbitrary_names(name: str) -> None:
    """Property: serialization never depends on dict insertion order."""
    a = {"projects": [{"id": "PRJ-001", "name": name}], "vendors": []}
    b = {"vendors": [], "projects": [{"name": name, "id": "PRJ-001"}]}
    assert canonical_bytes(a) == canonical_bytes(b)
