"""TR-001 / SC-002: four entries under /src, each owning its manifest."""

from __future__ import annotations

import pytest

from tests.checks.helpers.entries import (
    ALL_ENTRIES,
    PYTHON_ENTRIES,
    SRC_ROOT,
    javascript_lockfiles,
    manifest,
)


def test_exactly_four_entries_exist() -> None:
    found = sorted(p.name for p in SRC_ROOT.iterdir() if p.is_dir())
    assert found == sorted(ALL_ENTRIES), f"expected exactly {ALL_ENTRIES}, found {found}"


@pytest.mark.parametrize("entry", PYTHON_ENTRIES)
def test_each_python_entry_owns_a_manifest_and_lockfile(entry: str) -> None:
    assert (SRC_ROOT / entry / "pyproject.toml").is_file()
    assert (SRC_ROOT / entry / "uv.lock").is_file()


def test_web_entry_owns_a_manifest_and_exactly_one_lockfile() -> None:
    assert (SRC_ROOT / "web" / "package.json").is_file()
    locks = javascript_lockfiles()
    assert len(locks) == 1, f"expected exactly one JS lockfile, found {[p.name for p in locks]}"


@pytest.mark.parametrize("entry", PYTHON_ENTRIES)
def test_no_entry_declares_a_uv_workspace(entry: str) -> None:
    """A workspace would share one resolution and defeat the whole layout."""
    assert "workspace" not in manifest(entry).get("tool", {}).get("uv", {})
