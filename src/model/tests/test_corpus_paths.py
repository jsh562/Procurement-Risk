"""FR-017a / FR-018: corpus root, location discovery, and path containment.

Two of the properties here are **security controls**, and both are exercised in
their failing direction rather than described:

- **VR-009** — containment resolves the real path *first* and asserts second. A
  prefix test on the raw path is defeated by `..` segments the filesystem
  evaluates afterwards and by links that leave the tree, so this file feeds
  `resolve_within` an actual `..` escape and an actual symlink escape built in
  `tmp_path` and requires each to be refused.
- **VR-067** — discovery is non-following. A symbolic link to a regular file
  passes a link-following `is_file()` test *exactly*, which is why the
  prohibition is separate from `CHECK(is a regular file)`; the tests below
  plant that exact shape and require it classified as a link rather than as a
  document.

Symlink creation needs a privilege the Windows development machine may not
grant, so those cases skip rather than fail there. The Linux verification
runner is the platform of record and grants it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from model.corpus.paths import (
    DEFAULT_CORPUS_ROOT,
    LOCATION_ID_PATTERN,
    MANIFEST_FILENAME,
    REPO_ROOT,
    CorpusPathError,
    corpus_root,
    discover_locations,
    find_symlinks,
    resolve_within,
)
from model.roster.reader import DEFAULT_ROSTER_PATH


def link(source: Path, target: Path) -> Path:
    """Create a symbolic link or skip: the control cannot be shown otherwise."""
    try:
        source.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform dependent
        pytest.skip(f"this platform does not permit creating symbolic links: {exc}")
    return source


def a_location(root: Path, location_id: str, *documents: str) -> Path:
    directory = root.joinpath(*location_id.split("/"))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / MANIFEST_FILENAME).write_bytes(b"{}\n")
    for name in documents:
        (directory / name).write_bytes(b"%PDF-1.7\n")
    return directory


# --------------------------------------------------------------------------
# Discovery — a mechanical directory test, non-following (FR-006a, VR-067)
# --------------------------------------------------------------------------


def test_a_location_is_any_directory_holding_a_manifest(tmp_path: Path) -> None:
    a_location(tmp_path, "real/ufgs", "ufgs-26-05-00.pdf")
    a_location(tmp_path, "synthetic/PRJ-001", "sub-0001.pdf")
    (tmp_path / "synthetic" / "not-a-location").mkdir()

    found = discover_locations(tmp_path)
    assert [location.location_id for location in found] == ["real/ufgs", "synthetic/PRJ-001"]
    assert all(location.manifest_path.is_file() for location in found)
    assert all(location.conforms for location in found)


def test_the_root_and_its_intermediate_directories_are_not_locations(tmp_path: Path) -> None:
    a_location(tmp_path, "synthetic/PRJ-001")
    (tmp_path / "manifest.schema.json").write_bytes(b"{}\n")
    assert [location.location_id for location in discover_locations(tmp_path)] == [
        "synthetic/PRJ-001"
    ]


def test_discovery_is_sorted_rather_than_in_readdir_order(tmp_path: Path) -> None:
    """VR-040a names a shuffled directory enumeration as one of the dimensions
    two generator runs must differ in, so the platform's order must not reach a
    caller."""
    for project in ("PRJ-003", "PRJ-001", "PRJ-005", "PRJ-002", "PRJ-004"):
        a_location(tmp_path, f"synthetic/{project}")
    found = [location.location_id for location in discover_locations(tmp_path)]
    assert found == sorted(found)


def test_a_symlinked_directory_introduces_no_location_and_is_reported(tmp_path: Path) -> None:
    """VR-067. The walk does not descend into the link, so a location outside
    the corpus root cannot be introduced by one — and the link is named rather
    than silently skipped."""
    outside = tmp_path / "outside" / "elsewhere"
    outside.mkdir(parents=True)
    (outside / MANIFEST_FILENAME).write_bytes(b"{}\n")

    root = tmp_path / "corpus"
    a_location(root, "real/ufgs")
    link(root / "synthetic", outside)

    assert [location.location_id for location in discover_locations(root)] == ["real/ufgs"]
    assert find_symlinks(root) == (root / "synthetic",)


def test_a_symlinked_manifest_does_not_make_a_location(tmp_path: Path) -> None:
    """The case a link-following test admits exactly: the link points at a
    regular file, so `is_file()` would say yes and the directory would be read
    as a location whose manifest lives outside the repository."""
    real_manifest = tmp_path / "outside-manifest.json"
    real_manifest.write_bytes(b"{}\n")

    root = tmp_path / "corpus"
    directory = root / "synthetic" / "PRJ-009"
    directory.mkdir(parents=True)
    planted = link(directory / MANIFEST_FILENAME, real_manifest)

    assert planted.is_file()  # link-following: indistinguishable from a document
    assert discover_locations(root) == ()
    assert find_symlinks(root) == (planted,)


def test_a_symlinked_document_inside_a_location_is_found_as_a_link(tmp_path: Path) -> None:
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.7\n")
    root = tmp_path / "corpus"
    directory = a_location(root, "real/ufgs", "ufgs-26-05-00.pdf")
    planted = link(directory / "ufgs-23-64-26.pdf", outside)

    assert planted.is_file()
    assert find_symlinks(root) == (planted,)
    assert [location.location_id for location in discover_locations(root)] == ["real/ufgs"]


def test_a_clean_tree_reports_no_link(tmp_path: Path) -> None:
    """A control that cannot fail proves nothing; this is the negative side."""
    a_location(tmp_path, "real/ufgs", "ufgs-26-05-00.pdf")
    assert find_symlinks(tmp_path) == ()


def test_a_missing_corpus_root_fails_rather_than_reporting_nothing(tmp_path: Path) -> None:
    """VR-066: an empty or partially fetched checkout must be distinguishable
    from a clean one, which a 'no locations found' return value is not."""
    with pytest.raises(CorpusPathError):
        discover_locations(tmp_path / "absent")
    with pytest.raises(CorpusPathError):
        corpus_root(tmp_path / "absent")


def test_a_symlinked_corpus_root_is_refused(tmp_path: Path) -> None:
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    planted = link(tmp_path / "corpus", real_root)
    with pytest.raises(CorpusPathError) as raised:
        discover_locations(planted)
    assert "VR-067" in str(raised.value)


def test_the_default_corpus_root_is_data_corpus_under_the_repository_root() -> None:
    """FR-018 fixes the root; the derivation mirrors the roster reader's."""
    assert DEFAULT_CORPUS_ROOT == REPO_ROOT / "data" / "corpus"
    # The roster fixture is reached through the reader's own constant. Restating
    # its path here would make this file a second one naming it, which VR-045
    # forbids — the derivation is what is being mirrored, not the literal.
    assert DEFAULT_ROSTER_PATH.parent.parent == DEFAULT_CORPUS_ROOT.parent
    assert DEFAULT_ROSTER_PATH.is_file()


@pytest.mark.parametrize(
    ("location_id", "expected"),
    [
        ("real/ufgs", True),
        ("synthetic/PRJ-001", True),
        ("real", False),
        ("real/ufgs/nested", False),
        ("other/ufgs", False),
        ("real/-leading-dash", False),
    ],
)
def test_the_location_id_pattern_admits_two_segments_under_a_known_layer(
    location_id: str, expected: bool
) -> None:
    assert bool(LOCATION_ID_PATTERN.fullmatch(location_id)) is expected


# --------------------------------------------------------------------------
# Containment — resolve first, compare second (VR-009)
# --------------------------------------------------------------------------


def test_a_name_inside_the_base_resolves_to_the_file_it_names(tmp_path: Path) -> None:
    directory = a_location(tmp_path, "real/ufgs", "ufgs-26-05-00.pdf")
    resolved = resolve_within(directory, "ufgs-26-05-00.pdf")
    assert resolved == (directory / "ufgs-26-05-00.pdf").resolve()
    assert resolved.read_bytes().startswith(b"%PDF-")


def test_a_dot_dot_escape_is_refused_on_the_resolved_path(tmp_path: Path) -> None:
    """The `..` is *not* rejected lexically here — it is handed to the
    filesystem and refused on the path that came back, which is the ordering
    the rule states. A prefix check on the raw string would pass this."""
    directory = a_location(tmp_path, "real/ufgs")
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF-1.7\n")

    with pytest.raises(CorpusPathError) as raised:
        resolve_within(directory, "../../secret.pdf")
    message = str(raised.value)
    assert "VR-009" in message
    assert str(secret.resolve()) in message

    # And the raw join really does denote that file, so the refusal is the
    # control doing work rather than the path being harmless.
    assert (directory / "../../secret.pdf").resolve() == secret.resolve()


def test_a_dot_dot_sequence_that_stays_inside_is_admitted(tmp_path: Path) -> None:
    """Containment, not a ban on the segment: the rule is about where the path
    lands, and a rule that refused every `..` would be a different rule."""
    directory = a_location(tmp_path, "real/ufgs")
    (directory / "nested").mkdir()
    (directory / "ufgs-26-05-00.pdf").write_bytes(b"%PDF-1.7\n")
    assert (
        resolve_within(directory, "nested/../ufgs-26-05-00.pdf")
        == (directory / "ufgs-26-05-00.pdf").resolve()
    )


def test_a_symlink_escape_is_refused(tmp_path: Path) -> None:
    """The case ordering exists for: the raw path is a plain filename directly
    under the base and passes every string test, and only resolution shows it
    leaving the tree."""
    directory = a_location(tmp_path, "real/ufgs")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.7\n")
    planted = link(directory / "innocent.pdf", outside)

    assert planted.parent == directory  # the raw path is inside the base
    assert planted.is_file()  # and a link-following test says "regular file"

    with pytest.raises(CorpusPathError) as raised:
        resolve_within(directory, "innocent.pdf")
    message = str(raised.value)
    assert "VR-009" in message
    assert str(outside.resolve()) in message


def test_a_symlink_that_points_back_inside_is_still_refused(tmp_path: Path) -> None:
    """VR-067 is not a consequence of containment: this link lands somewhere
    admissible and is refused anyway, because a corpus file's `content_hash`
    must be over bytes a clone receives at that path."""
    directory = a_location(tmp_path, "real/ufgs", "ufgs-26-05-00.pdf")
    planted = link(directory / "alias.pdf", directory / "ufgs-26-05-00.pdf")

    assert planted.resolve().is_relative_to(directory.resolve())
    with pytest.raises(CorpusPathError) as raised:
        resolve_within(directory, "alias.pdf")
    assert "VR-067" in str(raised.value)


def test_a_symlinked_intermediate_directory_is_refused(tmp_path: Path) -> None:
    directory = a_location(tmp_path, "real/ufgs")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "document.pdf").write_bytes(b"%PDF-1.7\n")
    link(directory / "nested", elsewhere)

    with pytest.raises(CorpusPathError):
        resolve_within(directory, "nested/document.pdf")


@pytest.mark.parametrize("candidate", ["/etc/passwd", "\\\\server\\share\\x.pdf", ""])
def test_an_absolute_or_empty_candidate_is_refused_by_name(tmp_path: Path, candidate: str) -> None:
    """Joining an absolute path onto a base silently discards the base in
    `pathlib`, so this is refused explicitly rather than left to containment."""
    directory = a_location(tmp_path, "real/ufgs")
    with pytest.raises(CorpusPathError) as raised:
        resolve_within(directory, candidate)
    assert "VR-009" in str(raised.value)


# Every spelling a drive letter has, because the guard must not depend on the
# host: `pathlib` calls all of these relative when it runs on Linux. The
# original test asserted one of them and so guarded only that one -- the
# forward-slash form was refused on Windows and admitted on Linux, where it was
# joined onto the base as a two-segment relative path.
@pytest.mark.parametrize(
    "candidate",
    [
        "C:/Windows/win.ini",  # drive, root, forward slashes
        "C:\\Windows\\win.ini",  # drive, root, backslashes
        "c:/windows/win.ini",  # lower case is the same drive
        "C:win.ini",  # drive-relative: has a drive, absolute under neither flavour
    ],
)
def test_a_drive_letter_candidate_is_refused(tmp_path: Path, candidate: str) -> None:
    directory = a_location(tmp_path, "real/ufgs")
    with pytest.raises(CorpusPathError) as raised:
        resolve_within(directory, candidate)
    assert "VR-009" in str(raised.value)


def test_the_base_itself_is_not_a_containable_path(tmp_path: Path) -> None:
    directory = a_location(tmp_path, "real/ufgs")
    with pytest.raises(CorpusPathError):
        resolve_within(directory, ".")


def test_a_base_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "not-a-directory"
    target.write_bytes(b"")
    with pytest.raises(CorpusPathError):
        resolve_within(target, "anything.pdf")
