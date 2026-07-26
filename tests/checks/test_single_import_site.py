"""TR-010 / SC-014: the provider client is named in exactly one source file."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.checks.helpers.source_scan import format_violation, scan_source_root

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures"
PROVIDER_NAME = "anthropic"


def _mentions():
    return scan_source_root(SRC_ROOT, PROVIDER_NAME, fixture_root=FIXTURE_ROOT)


def test_provider_client_named_in_exactly_one_source_file() -> None:
    mentions = _mentions()
    assert len(mentions) == 1, format_violation(PROVIDER_NAME, mentions, REPO_ROOT)


def test_the_one_naming_file_is_the_gateway_provider_module() -> None:
    mentions = _mentions()
    assert mentions, "scan found no provider mention at all — the scan itself is broken"
    expected = SRC_ROOT / "gateway" / "src" / "gateway" / "provider.py"
    assert mentions[0].path == expected


def test_scan_reports_a_planted_second_site(tmp_path: Path) -> None:
    """Positive control: a scan that cannot fail proves nothing."""
    (tmp_path / "a.py").write_text("import anthropic\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("client = anthropic.Anthropic\n", encoding="utf-8")
    assert len(scan_source_root(tmp_path, PROVIDER_NAME)) == 2


@pytest.mark.parametrize("filename", ["uv.lock", "pyproject.toml", "package-lock.json"])
def test_manifests_and_lockfiles_are_outside_the_scanned_set(tmp_path: Path, filename: str) -> None:
    """These name the provider on a correct tree; scanning them fails always."""
    (tmp_path / filename).write_text('name = "anthropic"\n', encoding="utf-8")
    assert scan_source_root(tmp_path, PROVIDER_NAME) == []


# --- VR-013: exactly one module opens the roster -----------------------------
# Same mechanism and same scanned root as the provider scan above, deliberately.
# The roster has the same shape of risk: two readers means two definitions of
# the same data, and the second one is always the one nobody remembers exists.

ROSTER_FILENAME = "project-vendor-roster"


def test_exactly_one_module_opens_the_roster() -> None:
    mentions = scan_source_root(SRC_ROOT, ROSTER_FILENAME, fixture_root=FIXTURE_ROOT)
    assert len(mentions) == 1, format_violation(ROSTER_FILENAME, mentions, REPO_ROOT)


def test_the_one_roster_reader_lives_in_the_modeling_boundary() -> None:
    mentions = scan_source_root(SRC_ROOT, ROSTER_FILENAME, fixture_root=FIXTURE_ROOT)
    assert mentions, "scan found no roster reader at all"
    expected = SRC_ROOT / "model" / "src" / "model" / "roster" / "reader.py"
    assert mentions[0].path == expected


def test_the_serving_boundary_never_reads_the_roster() -> None:
    """TR-011 keeps the data directory out of the serving build context, so a
    serving-side reader would fail inside the image rather than at review."""
    assert not scan_source_root(SRC_ROOT / "api", ROSTER_FILENAME)


# --- VR-045: the corpus generator declares no project and no vendor ----------
# E002's generator obtains projects and vendors solely through
# `model.roster.reader.read_roster`. The mechanism is this same scan rather than
# a second one: a generator that opened the roster itself, or copied a project
# identifier into a literal, would be a second definition of E001's data with
# nothing comparing the two — and the second definition is always the one nobody
# remembers exists.


def test_vr_045_the_corpus_package_names_no_roster_path() -> None:
    """The count stays at one after E002 lands, and the one is E001's reader."""
    mentions = scan_source_root(SRC_ROOT, ROSTER_FILENAME, fixture_root=FIXTURE_ROOT)
    offenders = [
        mention
        for mention in mentions
        if mention.path != SRC_ROOT / "model" / "src" / "model" / "roster" / "reader.py"
    ]
    assert not offenders, "VR-045: " + format_violation(ROSTER_FILENAME, mentions, REPO_ROOT)


def test_vr_045_scan_reports_a_generator_that_opens_the_roster_itself(tmp_path: Path) -> None:
    """The failing direction, planted: a scan that cannot fail proves nothing.

    The planted module is shaped like the defect the rule refuses — a corpus
    module resolving the roster's own filename instead of calling the reader.
    """
    reader = tmp_path / "reader.py"
    reader.write_text("ROSTER = 'project-vendor-roster.json'\n", encoding="utf-8")
    generate = tmp_path / "generate.py"
    generate.write_text(
        "ROSTER = DATA / 'roster' / 'project-vendor-roster.json'\n", encoding="utf-8"
    )
    mentions = scan_source_root(tmp_path, ROSTER_FILENAME)
    assert len(mentions) == 2, "VR-045: " + format_violation(ROSTER_FILENAME, mentions, tmp_path)
    assert generate in {mention.path for mention in mentions}, (
        "VR-045: the scan missed a second module naming the roster path"
    )
