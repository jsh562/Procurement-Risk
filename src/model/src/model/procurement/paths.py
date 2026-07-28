"""Where E005's four emitted artifacts live, resolved in one place.

Every filename this epic writes is named here and nowhere else, following
`model.corpus.paths`: the generator, the loader, the validator and the
build-gating checks all ask this module rather than each spelling
`data/procurement/procurement-history.json` for themselves. A path spelled in
four places is four places for a rename to be half-applied, and DV-020 asserts
that the emitted set is *exactly* four files — a claim that can only be checked
against a closed enumeration, which `EMITTED_ARTIFACT_NAMES` is.

**Two trees, not one, and the separation is a requirement rather than
tidiness** (AD-007, FR-018). The fixture, its digest sidecar and the datasheet
sit under `data/procurement/`; the ground-truth record sits under
`data/ground-truth/`. The ground-truth record names the per-vendor offsets a
later fit is scored against, so it must lie outside every directory a
model-fitting entry point resolves as an input root. If it sat beside the
fixture, any job globbing the fixture's directory would read the answers.
Physical separation makes that check a directory assertion (DV-018) rather than
a filename exclusion list, which is what stops it rotting.

**Every function takes an optional repository root.** The default is this
checkout, derived from `__file__` exactly as `roster.reader` and `corpus.paths`
derive theirs. The parameter exists so a test can point the same resolution at a
`tmp_path` and exercise the real write path without touching the committed
artifacts — the alternative, letting tests build paths themselves, would leave
the tests agreeing with a second layout rather than with this one.

**No containment check, deliberately.** `corpus.paths.resolve_within` exists
because a corpus path arrives from a manifest, which is data, and data can carry
`..` or a symbolic link out of the tree. Nothing here is data: the directory and
file names are module constants and the only caller-supplied component is a root
a test chose. Adding a containment test would suggest a threat that is not
present and would obscure the one that is.

Stdlib only, following `model/roster/reader.py`.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "DATASHEET_FILENAME",
    "EMITTED_ARTIFACT_NAMES",
    "FIXTURE_FILENAME",
    "GROUND_TRUTH_DIR_PARTS",
    "HASH_FILENAME",
    "PROCUREMENT_DIR_PARTS",
    "REPO_ROOT",
    "TRUTH_FILENAME",
    "datasheet_path",
    "emitted_artifacts",
    "fixture_path",
    "ground_truth_dir",
    "hash_path",
    "procurement_dir",
    "truth_path",
]

# paths.py sits at src/model/src/model/procurement/, so the repository root is
# six levels up — the entry's own src-layout repeats the package name. Same
# derivation as `model/roster/reader.py` and `model/corpus/paths.py`,
# deliberately: three modules disagreeing about where the repository begins
# would be three different answers to one question.
REPO_ROOT = Path(__file__).resolve().parents[5]

#: The two trees, held as path *parts* rather than as strings with separators,
#: so no caller has to know which slash this platform uses.
PROCUREMENT_DIR_PARTS = ("data", "procurement")
GROUND_TRUTH_DIR_PARTS = ("data", "ground-truth")

FIXTURE_FILENAME = "procurement-history.json"
HASH_FILENAME = "procurement-history.hash.json"
DATASHEET_FILENAME = "datasheet.md"
TRUTH_FILENAME = "vendor-offsets.json"

#: The closed emitted set, as repository-relative POSIX strings. DV-020 forbids
#: a train/evaluation split artifact, and states that prohibition as a positive
#: condition — the emitted set is exactly these four — because an open negative
#: ("no split file is written") is not checkable and this is.
EMITTED_ARTIFACT_NAMES = (
    "/".join((*PROCUREMENT_DIR_PARTS, FIXTURE_FILENAME)),
    "/".join((*PROCUREMENT_DIR_PARTS, HASH_FILENAME)),
    "/".join((*PROCUREMENT_DIR_PARTS, DATASHEET_FILENAME)),
    "/".join((*GROUND_TRUTH_DIR_PARTS, TRUTH_FILENAME)),
)


def _root(root: Path | str | None) -> Path:
    """The repository root to resolve against: the caller's, or this checkout's.

    `None` means "this checkout" rather than "the current working directory".
    A relative default would make every path here depend on where the process
    was started, and the generator is run from `src/model` while the artifacts
    live at the repository root.
    """
    return REPO_ROOT if root is None else Path(root)


def procurement_dir(root: Path | str | None = None) -> Path:
    """The directory holding the fixture, its digest sidecar and the datasheet."""
    return _root(root).joinpath(*PROCUREMENT_DIR_PARTS)


def ground_truth_dir(root: Path | str | None = None) -> Path:
    """The isolated tree holding the ground-truth record, and nothing else.

    Separate from `procurement_dir` by requirement (FR-018, AD-007), not by
    preference. See the module docstring.
    """
    return _root(root).joinpath(*GROUND_TRUTH_DIR_PARTS)


def fixture_path(root: Path | str | None = None) -> Path:
    """`data/procurement/procurement-history.json` — the hashed payload.

    One canonical JSON object: the envelope, then `lines[]` with nested
    `events[]` (AD-002). The digest FR-021 compares against is taken over the
    canonical re-serialization of this file's *parsed* content, not over its
    bytes, so the file's indentation and line endings are free to change without
    moving the oracle.
    """
    return procurement_dir(root) / FIXTURE_FILENAME


def hash_path(root: Path | str | None = None) -> Path:
    """`data/procurement/procurement-history.hash.json` — the digest sidecar.

    A sidecar rather than a field inside the fixture, because a digest cannot
    live inside the payload it covers, and a sidecar rather than a line in the
    datasheet, because that would make the reproducibility oracle a Markdown
    parse.
    """
    return procurement_dir(root) / HASH_FILENAME


def datasheet_path(root: Path | str | None = None) -> Path:
    """`data/procurement/datasheet.md` — the seven-section disclosure.

    Emitted by the generator rather than hand-written, so every realized figure
    in it is written by the same run that wrote the fixture and cannot drift
    from it.
    """
    return procurement_dir(root) / DATASHEET_FILENAME


def truth_path(root: Path | str | None = None) -> Path:
    """`data/ground-truth/vendor-offsets.json` — the isolated ground truth.

    Holds the per-vendor log offsets, the realized spreads and the variance
    decomposition: the answers a later hierarchical fit is scored against. It is
    bound to one fixture by `dataset_content_hash`, so "these are the offsets
    that dataset was generated from" is checkable rather than asserted.
    """
    return ground_truth_dir(root) / TRUTH_FILENAME


def emitted_artifacts(root: Path | str | None = None) -> tuple[Path, ...]:
    """The four artifacts a complete generation run writes, in emission order.

    Returned as a tuple rather than a set: DV-023 forbids hash-ordered iteration
    from reaching the write path, and a set is exactly that.
    """
    return (fixture_path(root), hash_path(root), datasheet_path(root), truth_path(root))
