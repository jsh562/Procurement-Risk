"""The corpus root, non-following location discovery, and path containment.

FR-017a / FR-018, and the two rules here that are security controls rather than
conveniences: VR-009 (containment, with its order of operations fixed) and
VR-067 (no symbolic link anywhere under the corpus root).

**A corpus location is any directory containing `manifest.json`.** That is a
mechanical directory test, so "is this file inside a location" is decidable
without reading anything — in particular without first reading the manifest
whose license basis an earlier phrasing depended on. The corpus root and its
intermediate directories contain no `manifest.json` and are therefore not
locations.

**Discovery does not follow links** (VR-067). Every classification below uses
`os.scandir(follow_symlinks=False)` and `DirEntry.is_symlink()`, never a
link-following `is_file()` or `is_dir()`. The prohibition is stated separately
from `CHECK(is a regular file)` because a symbolic link to a regular file
elsewhere on the machine satisfies a link-following regular-file test
*exactly*, and its `content_hash` would then be computed over bytes that live
outside the repository and are not what a clone receives (CWE-59, CWE-61,
CWE-64, CWE-73). The walk also never descends into a link, so a symlinked
directory cannot introduce a location outside the root and a link cycle cannot
arise for the walk to loop on.

**Containment resolves first and compares second** (VR-009). A prefix test on
the raw path is defeated by `..` segments the filesystem evaluates afterwards
and by links that leave the tree; the ordering is the control, not the
comparison. `resolve_within` therefore accepts a `..` sequence as *input* and
rejects it on the resolved path, which is why it can be demonstrated failing
rather than merely described (CWE-22, CWE-23, CWE-36).

Results are sorted, deliberately: VR-040a names a shuffled directory
enumeration as one of the environment dimensions two generator runs must differ
in, so discovery must not pass the platform's `readdir` order through to a
caller.

Stdlib only, following `model/roster/reader.py`; one error type.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

__all__ = [
    "DEFAULT_CORPUS_ROOT",
    "LOCATION_ID_PATTERN",
    "MANIFEST_FILENAME",
    "REPO_ROOT",
    "CorpusLocation",
    "CorpusPathError",
    "corpus_root",
    "discover_locations",
    "find_symlinks",
    "repository_relative_path",
    "resolve_within",
]

# paths.py sits at src/model/src/model/corpus/, so the repository root is six
# levels up — the entry's own src-layout repeats the package name. Same
# derivation as `model/roster/reader.py`, deliberately.
REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_CORPUS_ROOT = REPO_ROOT / "data" / "corpus"

MANIFEST_FILENAME = "manifest.json"

# `location_id` is the POSIX path of a location relative to the corpus root.
# Two segments exactly: the layer directory and the location's own name.
LOCATION_ID_PATTERN = re.compile(r"^(real|synthetic)/[A-Za-z0-9][A-Za-z0-9-]*$")


class CorpusPathError(ValueError):
    """Raised when a corpus path is unreadable, escapes its base, or is a link.

    One type for every failure, as `RosterError` is: a caller learns the same
    thing from each of them — this path must not be opened.
    """


@dataclass(frozen=True)
class CorpusLocation:
    """A discovered corpus location: its identifier and its directory.

    Discovery reports what is on disk and judges none of it. A `location_id`
    that does not match `LOCATION_ID_PATTERN` is still returned, because VR-004
    compares discovered locations against declared ones and cannot do so over a
    set discovery has already filtered.
    """

    location_id: str
    path: Path

    @property
    def manifest_path(self) -> Path:
        return self.path / MANIFEST_FILENAME

    @property
    def conforms(self) -> bool:
        return LOCATION_ID_PATTERN.fullmatch(self.location_id) is not None


def corpus_root(root: Path | None = None) -> Path:
    """The corpus root, checked before anything walks it.

    Fails rather than returning an empty result for a missing root: an empty or
    partially fetched checkout must be distinguishable from a clean one, which
    a "no locations found" return value is not (VR-066).
    """
    candidate = Path(root) if root is not None else DEFAULT_CORPUS_ROOT
    if candidate.is_symlink():
        raise CorpusPathError(f"VR-067: the corpus root is a symbolic link: {candidate}")
    if not candidate.exists():
        raise CorpusPathError(f"corpus root does not exist: {candidate}")
    if not candidate.is_dir():
        raise CorpusPathError(f"corpus root is not a directory: {candidate}")
    return candidate


@dataclass(frozen=True)
class _Scan:
    """One non-following walk's findings, kept apart rather than merged.

    `links` is a first-class result, not a residue: a link that is silently
    skipped is a link nobody reports, and VR-067 requires it named.
    """

    directories: tuple[Path, ...]
    files: tuple[Path, ...]
    links: tuple[Path, ...]


def _scan(root: Path) -> _Scan:
    directories: list[Path] = []
    files: list[Path] = []
    links: list[Path] = []

    pending = [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    # `DirEntry.is_symlink()` never follows, by definition. It
                    # is asked first so a link is classified as a link and not
                    # as whatever it points at.
                    if entry.is_symlink():
                        links.append(path)
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        directories.append(path)
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(path)
                    else:
                        # A socket, fifo or device node is neither, and is
                        # reported as a file so the corpus-root closure rules
                        # (VR-060, VR-065) see it rather than skip it.
                        files.append(path)
        except OSError as exc:
            raise CorpusPathError(f"cannot read {current}: {exc}") from exc

    key = Path.as_posix
    return _Scan(
        directories=tuple(sorted(directories, key=key)),
        files=tuple(sorted(files, key=key)),
        links=tuple(sorted(links, key=key)),
    )


def discover_locations(root: Path | None = None) -> tuple[CorpusLocation, ...]:
    """Every directory under the corpus root holding a `manifest.json` (FR-006a).

    A directory walk rather than an aggregate index (FR-006a): there is no file
    listing the locations, so nothing can disagree with the filesystem about
    what exists. The exposure that creates — a whole location deleted and
    simply not discovered — is closed by the population rules VR-005 and
    VR-025, not by an index.

    The `manifest.json` test is non-following: a directory whose manifest is a
    symbolic link is **not** a location, and that link is reported by
    `find_symlinks` rather than resolved into an admission.
    """
    base = corpus_root(root)
    scan = _scan(base)
    manifests = {path.parent for path in scan.files if path.name == MANIFEST_FILENAME}
    return tuple(
        CorpusLocation(location_id=directory.relative_to(base).as_posix(), path=directory)
        for directory in sorted(manifests, key=Path.as_posix)
    )


def find_symlinks(root: Path | None = None) -> tuple[Path, ...]:
    """Every symbolic link anywhere under the corpus root (VR-067).

    Returned rather than raised so the validator can collect all failures and
    name each path (VR-056). The links are found by the same non-following walk
    `discover_locations` uses, so the two cannot disagree about what the tree
    holds.
    """
    return _scan(corpus_root(root)).links


def repository_relative_path(repo_relative: str, root: Path | None = None) -> Path:
    """Resolve a repository-relative corpus path under the corpus root.

    The generation inputs of FR-009b are recorded as **repository-relative**
    strings (`data/corpus/synthetic/…`) because that is the form a manifest
    carries, while every path rule here is expressed relative to the corpus
    root. This converts between the two once, so no module has to restate the
    `data/corpus` prefix as a literal: the prefix is derived from
    `DEFAULT_CORPUS_ROOT` and `REPO_ROOT` rather than written down again.

    The string is passed in rather than imported from `manifest.py` — that
    module already imports this one, and the closed three-value set stays where
    the entry that records it lives.

    Resolution goes through `resolve_within`, so a generation input is held to
    the same containment ordering and link prohibition as a corpus document
    (VR-009, VR-067). The target need not exist; a caller's own open reports
    that, and reporting it here would make the two failures indistinguishable.
    """
    prefix = DEFAULT_CORPUS_ROOT.relative_to(REPO_ROOT).as_posix() + "/"
    if not isinstance(repo_relative, str) or not repo_relative.startswith(prefix):
        raise CorpusPathError(
            f"{repo_relative!r} is not a repository-relative path under {prefix!r}"
        )
    return resolve_within(corpus_root(root), repo_relative[len(prefix) :])


def resolve_within(base: Path, candidate: str | Path) -> Path:
    """Resolve `candidate` under `base` and assert it stayed there (VR-009).

    The order of operations is the control:

    1. an absolute candidate is refused outright — joining one onto a base
       silently discards the base in `pathlib`, so this is refused by name
       rather than left to the containment test to notice;
    2. the base is resolved to its real path;
    3. the candidate is joined and **then** resolved — links followed, `.` and
       `..` collapsed by the filesystem rather than by string arithmetic;
    4. containment is asserted on the *resolved* pair. A comparison performed
       before resolution is defeated by segments the filesystem evaluates
       afterwards;
    5. only then, with the path known to be inside, is any component tested for
       link-ness, so a link that points back inside the base is still refused
       (VR-067) rather than accepted because it happens to land somewhere
       admissible.

    The declared base is one value — for a manifest entry, the entry's own
    corpus location directory. `data/corpus/` and `data/` are not alternative
    bases; a real path under the location directory is under both of them by
    construction.
    """
    base_path = Path(base)
    if not base_path.is_dir():
        raise CorpusPathError(f"containment base is not a directory: {base_path}")

    raw = Path(candidate)
    text = str(candidate)

    # Absoluteness is judged under both path flavours rather than the host's.
    # A manifest is portable data read on Windows and on Linux, but `pathlib`
    # answers `is_absolute()` and `.drive` against the running OS only: to a
    # `PosixPath`, "C:/Windows/win.ini" is a *relative* path two segments deep
    # with an oddly named first component, and it carries no drive at all. The
    # single-flavour test that stood here therefore refused a drive letter on
    # Windows and admitted the identical string on Linux, where it was joined
    # onto the base and resolved — the guard's verdict depended on who ran it.
    #
    # Whether a corpus path is relative is a property of the string, so both
    # flavours are asked and either one objecting is enough. The Windows drive
    # test is kept alongside its `is_absolute()` because a drive-relative path
    # ("C:doc.pdf") has a drive and no root, and is absolute under neither.
    windows_view = PureWindowsPath(text)
    if (
        not text
        or PurePosixPath(text).is_absolute()
        or windows_view.is_absolute()
        or windows_view.drive
        or text.startswith(("/", "\\"))
    ):
        raise CorpusPathError(
            f"VR-009: {text!r} is absolute or empty; a corpus path is relative to its location"
        )

    resolved_base = base_path.resolve()
    resolved = (resolved_base / raw).resolve()

    if resolved == resolved_base or not resolved.is_relative_to(resolved_base):
        raise CorpusPathError(
            f"VR-009: {text!r} resolves to {resolved}, which is outside {resolved_base}"
        )

    # Walk down from the base one recorded segment at a time, over the literal
    # parts rather than the resolved path: resolution has already replaced
    # every link with its target, so a link is only visible before it.
    for depth in range(1, len(raw.parts) + 1):
        probe = resolved_base.joinpath(*raw.parts[:depth])
        if probe.is_symlink():
            raise CorpusPathError(f"VR-067: {probe} is a symbolic link and is not followed")

    return resolved
