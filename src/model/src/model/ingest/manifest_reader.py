"""Reading the committed manifests, and hash-verifying every file before parse.

FR-001 / FR-005. E002 owns the manifest *writer* — `model.corpus.manifest` — and
the private read path inside `model.corpus.validate`, which is a rule engine
returning failure records rather than entries. Neither is a public reader, so
this module is the one the ingestion job enumerates the corpus through. It is
deliberately a reader and nothing else: no row is written here, no document is
parsed here, and the only judgement it makes is whether an entry may be opened.

**Enumeration is through the manifests, never the filesystem** (FR-001). A PDF
sitting in a corpus location that no manifest lists is not ingested and is not
reported here as a document — the corpus validator (E002, VR-011) owns the
entry↔file bijection, and a second opinion about it in this module would be a
second answer. What this module refuses is the opposite direction: a manifest
entry naming a file that is absent, unreadable, or outside its location.

**Every path resolves through `corpus.paths.resolve_within`** (FR-001). The
declared base is the entry's own location directory, which is the base VR-009
names. A manifest is externally controlled data as far as this job is concerned
— it is committed, but the job must not be the thing that makes a traversal
sequence in it reachable — so the containment ordering and the link prohibition
are inherited from E002 rather than re-implemented at a weaker strength.

**Verification precedes parsing, and its failure is fatal to the run**
(FR-005). `verify_hash` recomputes `content_hash` over the file's bytes with
E002's own `content_hash_of_file` and raises on disagreement. It raises rather
than returning a flag because FR-005 requires the run to fail with **zero rows
written**, and a boolean a caller may ignore is exactly how a mismatched
document reaches a transaction.

**Entries are re-constructed as E002's own types.** `RealEntry` and
`SyntheticEntry` validate every recorded value in `__post_init__`, and the layer
asymmetry is enforced by which class exists rather than by a check here. A
manifest carrying retrieval provenance on a generated document therefore fails
at construction, in E002's code, which is the one place that rule is written.

Stdlib plus `model.corpus`, in the house style of `model/corpus/paths.py`: one
error type, frozen dataclasses, sorted results.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from model.corpus.manifest import (
    LAYER_REAL,
    LAYER_SYNTHETIC,
    ManifestEntry,
    ManifestError,
    RealEntry,
    RealLicenseBasis,
    SyntheticEntry,
    SyntheticLicenseBasis,
    content_hash_of_file,
)
from model.corpus.paths import (
    CorpusLocation,
    CorpusPathError,
    discover_locations,
    resolve_within,
)

__all__ = [
    "CorpusDocument",
    "ManifestReadError",
    "iter_entries",
    "manifest_digests",
    "verify_hash",
]


class ManifestReadError(ValueError):
    """Raised when a manifest cannot be read or an entry cannot be opened.

    One type for every failure, as `CorpusPathError` and `ManifestError` are: a
    caller learns the same thing from each of them — this corpus must not be
    ingested, and the run fails before the first document transaction.
    """


@dataclass(frozen=True)
class CorpusDocument:
    """One manifest entry, resolved to a file the job may open.

    The entry is carried whole rather than flattened into fields. FR-004
    requires layer, license basis and layer-appropriate provenance to reach the
    document record *unchanged*, and the cheapest way to keep that true is to
    hand the consumer the object the manifest was parsed into rather than a
    copy this module decided the shape of.
    """

    location_id: str
    layer: str
    project_id: str | None
    entry: ManifestEntry
    path: Path

    @property
    def stem(self) -> str:
        """The file stem FR-002 mints an identifier from."""
        return Path(self.entry.location).stem

    @property
    def content_hash(self) -> str:
        return self.entry.content_hash


def _read_manifest_document(path: Path) -> Mapping[str, object]:
    """Parse one `manifest.json`: UTF-8, no BOM, no duplicate key, an object.

    The duplicate-key refusal matches E002's reader (VR-001). A last-wins merge
    would let a manifest carrying two `entries` arrays be ingested through
    whichever one came second, with nothing anywhere reporting the other.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManifestReadError(f"cannot read {path}: {exc}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ManifestReadError(f"{path} begins with a UTF-8 BOM")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestReadError(f"{path} is not valid UTF-8: {exc}") from exc
    try:
        document = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        raise ManifestReadError(f"{path} does not parse as JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ManifestReadError(f"{path} must hold a JSON object, found {type(document).__name__}")
    return document


def _reject_duplicate_keys(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def _entry_from(payload: Mapping[str, object], where: str) -> ManifestEntry:
    """Build E002's own entry type from one manifest record.

    The layer decides the class, and the class decides which fields exist. A
    `SYNTHETIC` record carrying `source_location` therefore raises a
    `TypeError` from the dataclass constructor — an unexpected keyword — which
    is translated here rather than allowed to surface as an internal error.
    """
    if not isinstance(payload, Mapping):
        raise ManifestReadError(f"{where}: an entry must be a JSON object")
    fields = {str(key): value for key, value in payload.items()}
    layer = fields.pop("layer", None)
    basis = fields.pop("license_basis", None)
    if not isinstance(basis, Mapping):
        raise ManifestReadError(f"{where}: license_basis must be a JSON object")
    basis_fields = {str(key): value for key, value in basis.items()}

    try:
        if layer == LAYER_REAL:
            return RealEntry(license_basis=RealLicenseBasis(**basis_fields), **fields)
        if layer == LAYER_SYNTHETIC:
            return SyntheticEntry(license_basis=SyntheticLicenseBasis(**basis_fields), **fields)
    except ManifestError as exc:
        raise ManifestReadError(f"{where}: {exc}") from exc
    except TypeError as exc:
        # A field belonging to the other layer, or a missing required one.
        raise ManifestReadError(f"{where}: entry does not fit layer {layer!r}: {exc}") from exc
    raise ManifestReadError(f"{where}: layer must be REAL or SYNTHETIC, found {layer!r}")


def _location_documents(location: CorpusLocation) -> tuple[CorpusDocument, ...]:
    document = _read_manifest_document(location.manifest_path)

    declared = document.get("location_id")
    if declared != location.location_id:
        raise ManifestReadError(
            f"{location.manifest_path}: location_id {declared!r} does not match the "
            f"directory it was found in ({location.location_id!r})"
        )

    entries = document.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ManifestReadError(f"{location.manifest_path}: entries must be a non-empty JSON array")

    project_id = document.get("project_id")
    if project_id is not None and not isinstance(project_id, str):
        raise ManifestReadError(f"{location.manifest_path}: project_id must be a string")

    resolved: list[CorpusDocument] = []
    for index, payload in enumerate(entries):
        where = f"{location.manifest_path} entries[{index}]"
        entry = _entry_from(payload, where)
        declared_layer = document.get("layer")
        if entry.layer != declared_layer:
            raise ManifestReadError(
                f"{where}: entry layer {entry.layer!r} does not match the manifest's "
                f"{declared_layer!r}"
            )
        try:
            path = resolve_within(location.path, entry.location)
        except CorpusPathError as exc:
            raise ManifestReadError(f"{where}: {exc}") from exc
        if not path.is_file():
            raise ManifestReadError(f"{where}: {entry.location} is not a file at {path}")
        resolved.append(
            CorpusDocument(
                location_id=location.location_id,
                layer=entry.layer,
                project_id=project_id,
                entry=entry,
                path=path,
            )
        )
    # `location` is the entry's key within a manifest (VR-011), so sorting on it
    # is a total order and the enumeration is the same on every platform,
    # whatever order the file happened to list them in.
    return tuple(sorted(resolved, key=lambda document: document.entry.location))


def iter_entries(root: Path | None = None) -> Iterator[CorpusDocument]:
    """Every manifest entry in the corpus, resolved and in a fixed order.

    Ordered by `(location_id, location)` — the corpus is enumerated the same way
    on every machine, which is one of the conditions FR-017's determinism claim
    rests on. Yields lazily so a caller may verify and parse one document at a
    time, but discovery and manifest parsing happen per location, so a malformed
    manifest fails before any entry of that location is yielded.
    """
    try:
        locations = discover_locations(root)
    except CorpusPathError as exc:
        raise ManifestReadError(str(exc)) from exc
    if not locations:
        raise ManifestReadError("no corpus location holds a manifest.json")
    for location in locations:
        yield from _location_documents(location)


def verify_hash(document: CorpusDocument) -> str:
    """Recompute `content_hash` over the file's bytes and refuse a mismatch.

    Called **before** the document is parsed (FR-005). Returns the observed
    digest so a caller can record it; raises `ManifestReadError` when it differs
    from the manifest's, naming both values, so the run fails with zero rows
    rather than ingesting bytes no manifest describes.
    """
    if not isinstance(document, CorpusDocument):
        raise ManifestReadError(f"expected a CorpusDocument, found {type(document).__name__}")
    try:
        observed = content_hash_of_file(document.path)
    except ManifestError as exc:
        raise ManifestReadError(f"cannot digest {document.path}: {exc}") from exc
    if observed != document.content_hash:
        raise ManifestReadError(
            f"FR-005: {document.path} does not match its manifest content_hash; "
            f"recorded {document.content_hash}, found {observed}"
        )
    return observed


def manifest_digests(root: Path | None = None) -> tuple[str, ...]:
    """Each corpus manifest's own file digest, in location order.

    `ingestion_run.corpus_manifest_digests` records which manifests a run
    enumerated. Taken over the manifest files' raw bytes rather than over the
    entries they hold, so an edit anywhere in a manifest — including one this
    reader ignores — moves the recorded value.
    """
    try:
        locations = discover_locations(root)
    except CorpusPathError as exc:
        raise ManifestReadError(str(exc)) from exc
    try:
        return tuple(content_hash_of_file(location.manifest_path) for location in locations)
    except ManifestError as exc:
        raise ManifestReadError(str(exc)) from exc
