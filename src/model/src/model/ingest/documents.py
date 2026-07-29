"""Document identifiers, the closed type set, and the record a document becomes.

FR-002 / FR-003 / FR-004 / FR-006 / FR-052. Between the manifest reader and the
writer sits exactly one decision per document: what it is called, what kind of
thing it is, which project it belongs to, and which provenance it is entitled to
carry. This module makes all four and writes nothing.

**The identifier transform is stated, not chosen per file** (FR-002). Three
steps, in order: lower-case the file stem, replace every run of characters
outside `[a-z0-9]` with a single hyphen, strip a leading or trailing hyphen.
`UFGS-23-52-00.pdf` yields `ufgs-23-52-00` and `PRJ-001-T0002-R0.pdf` yields
`prj-001-t0002-r0`. A stem whose transform does not satisfy E003's
`ck_document__id_format` — `^[a-z0-9]+(-[a-z0-9]+)*$` at 3 to 128 characters —
**fails the run naming the file**. It is not truncated, padded, or coerced: each
of those silently maps two source files onto one record, which is the collision
FR-052 exists to refuse, arriving by a route FR-052 cannot see.

**`PRJ-000` is a convention, and the code says so** (FR-003, AD-007). Real
specifications are governing documents shared by every project, so they are
recorded under the reserved shared-library project rather than fanned out per
referencing project or chunked once per project. Nothing structural reserves it
— `ck_document__project_id_format` admits `PRJ-000` like any other — so this
module refuses to mint it for a synthetic document and the ingestion report
publishes the convention and the absence of enforcement (FR-003).

**Provenance is carried by layer, and the other layer's fields are absent**
(FR-004). E003's `document` table pairs every provenance column with two checks:
required on its own layer, rejected on the other. That is enforced again here,
before the row exists, because a generated document carrying a fabricated
issuing body is indistinguishable downstream from a verified one. `RealEntry`
and `SyntheticEntry` have no attribute slot for the other layer's fields at all,
so the mapping below cannot accidentally cross them.

**The collision check is corpus-wide and precedes every transaction** (FR-052).
`build_documents` mints over the whole enumerated corpus and raises naming
**both** files before returning, so a colliding run writes zero rows rather than
overwriting one record or attaching one document's chunks to another.

Stdlib plus `model.corpus` and this package's manifest reader. One error type.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from model.corpus.manifest import (
    LAYER_REAL,
    LAYER_SYNTHETIC,
    RealEntry,
    SyntheticEntry,
)
from model.ingest.manifest_reader import CorpusDocument

__all__ = [
    "DOCUMENT_ID_PATTERN",
    "DOCUMENT_TYPES",
    "MAX_DOCUMENT_ID_LENGTH",
    "MIN_DOCUMENT_ID_LENGTH",
    "PROJECT_ID_PATTERN",
    "SHARED_LIBRARY_PROJECT",
    "TYPE_BY_LAYER",
    "DocumentRecord",
    "DocumentError",
    "build_document",
    "build_documents",
    "classify_type",
    "mint_document_id",
]

#: E003's `ck_document__id_format`, restated here so the transform is checked
#: against the storage rule before a row is built rather than by the database
#: after 50 other documents have committed.
DOCUMENT_ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MIN_DOCUMENT_ID_LENGTH = 3
MAX_DOCUMENT_ID_LENGTH = 128

#: E003's `ck_document__project_id_format`.
PROJECT_ID_PATTERN = re.compile(r"^PRJ-[0-9]{3}$")

#: AD-007 / FR-003. Reserved by convention, published by the report, and
#: refused here for any synthetic document.
SHARED_LIBRARY_PROJECT = "PRJ-000"

#: FR-006's closed set, as this epic uses it. E003's `ck_document__type` admits
#: seven values; this corpus holds two kinds of thing and no ingestion path
#: invents a third — a real UFGS section is a `specification`, a generated
#: submittal transmittal is a `transmittal`.
TYPE_BY_LAYER: Mapping[str, str] = {
    LAYER_REAL: "specification",
    LAYER_SYNTHETIC: "transmittal",
}
DOCUMENT_TYPES: frozenset[str] = frozenset(TYPE_BY_LAYER.values())

_OUTSIDE_ALPHABET = re.compile(r"[^a-z0-9]+")


class DocumentError(ValueError):
    """Raised when a document cannot be named, typed, or given its provenance.

    One type for every failure: a caller learns the same thing from each of them
    — this corpus must not be ingested, and the run fails before the first
    document transaction commits.
    """


def mint_document_id(stem: str, *, source: str | None = None) -> str:
    """FR-002's three-step transform, with the result checked against E003.

    `source` names the file in the failure message. It is optional only so the
    transform can be exercised on a bare string; every call from the ingestion
    path supplies it, because "a stem does not transform" is unactionable
    without the file it came from.
    """
    where = f" ({source})" if source else ""
    if not isinstance(stem, str):
        raise DocumentError(f"a document identifier is minted from a string{where}")

    # Step 1 — lower-case. Step 2 — every *run* outside the alphabet becomes one
    # hyphen, so `a__b` and `a-b` mint the same identifier and the collision is
    # then reported by FR-052 rather than by two records differing in punctuation.
    # Step 3 — strip a leading or trailing hyphen, which is what a stem beginning
    # or ending in a separator would otherwise leave.
    minted = _OUTSIDE_ALPHABET.sub("-", stem.lower()).strip("-")

    if not minted:
        raise DocumentError(
            f"FR-002: the stem {stem!r} transforms to an empty identifier{where}; "
            "it is not padded or coerced"
        )
    if not DOCUMENT_ID_PATTERN.fullmatch(minted):
        raise DocumentError(
            f"FR-002: the stem {stem!r} transforms to {minted!r}{where}, which does not "
            f"satisfy {DOCUMENT_ID_PATTERN.pattern}"
        )
    if not MIN_DOCUMENT_ID_LENGTH <= len(minted) <= MAX_DOCUMENT_ID_LENGTH:
        raise DocumentError(
            f"FR-002: the stem {stem!r} transforms to {minted!r}{where}, which is "
            f"{len(minted)} characters and outside "
            f"{MIN_DOCUMENT_ID_LENGTH}..{MAX_DOCUMENT_ID_LENGTH}; "
            "it is not truncated or padded"
        )
    return minted


def classify_type(layer: str) -> str:
    """FR-006: the document type, from the closed set, decided by the layer.

    A layer outside the two E002 records raises rather than defaulting. A
    default here would invent a type for a document nobody classified, which is
    exactly what "never invent a type outside it" forbids.
    """
    try:
        return TYPE_BY_LAYER[layer]
    except KeyError:
        raise DocumentError(
            f"FR-006: no document type for layer {layer!r}; the closed set is "
            f"{sorted(TYPE_BY_LAYER)}"
        ) from None


@dataclass(frozen=True)
class DocumentRecord:
    """One row of E003's `document`, before it is written.

    Field names are E003's column names. The layer-conditional columns are
    `None` on the layer that rejects them, which is the same shape the table's
    paired checks admit — so a record that would be refused at the storage
    boundary is unconstructible here rather than caught 50 documents later.
    """

    document_id: str
    document_type: str
    project_id: str
    title: str
    source_kind: str
    license_basis: str
    content_hash: str
    path: Path
    source_ref: str | None = None
    issuing_body: str | None = None
    retrieval_date: date | None = None
    generator_id: str | None = None
    generation_seed: str | None = None
    generated_at: date | None = None
    fixture_hashes: tuple[str, ...] | None = None
    roster_hash: str | None = None

    def __post_init__(self) -> None:
        if self.document_type not in DOCUMENT_TYPES:
            raise DocumentError(f"FR-006: {self.document_type!r} is outside the closed set")
        if not PROJECT_ID_PATTERN.fullmatch(self.project_id):
            raise DocumentError(
                f"{self.document_id}: project_id {self.project_id!r} does not match "
                f"{PROJECT_ID_PATTERN.pattern}"
            )
        if not self.title.strip():
            raise DocumentError(f"{self.document_id}: title must not be blank")
        if not self.license_basis.strip():
            raise DocumentError(f"{self.document_id}: license_basis must not be blank")
        real = self.source_kind == LAYER_REAL
        synthetic = self.source_kind == LAYER_SYNTHETIC
        if not (real or synthetic):
            raise DocumentError(f"{self.document_id}: source_kind {self.source_kind!r} is neither")
        # FR-004, in both directions — the shape E003 states as a pair of checks
        # per column. Written as one comparison over two field groups so a new
        # column cannot join one direction and miss the other.
        real_only = (self.source_ref, self.issuing_body, self.retrieval_date)
        synthetic_only = (
            self.generator_id,
            self.generation_seed,
            self.generated_at,
            self.fixture_hashes,
            self.roster_hash,
        )
        present = real_only if real else synthetic_only
        absent = synthetic_only if real else real_only
        if any(value is None for value in present):
            raise DocumentError(
                f"FR-004: {self.document_id} is {self.source_kind} and is missing a "
                f"provenance value its layer requires"
            )
        if any(value is not None for value in absent):
            raise DocumentError(
                f"FR-004: {self.document_id} is {self.source_kind} and carries provenance "
                f"belonging to the other layer"
            )


def _canonical_license_basis(payload: Mapping[str, object]) -> str:
    """FR-004's "unchanged", serialized into E003's one text column.

    The manifest's license basis is a structured object of four components;
    `document.license_basis` is one non-empty text column. Recording only the
    `basis_id` would carry the *governing* component and silently drop the
    statute, the document identifier, and the point-of-use check — a change by
    omission, which "unchanged" does not admit. The whole object is therefore
    serialized canonically (sorted keys, no incidental whitespace), so the
    recorded text is a function of the manifest's content alone and the basis id
    is still recoverable by a reader that wants only that.
    """
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _real_record(document: CorpusDocument, document_id: str) -> DocumentRecord:
    entry = document.entry
    assert isinstance(entry, RealEntry)  # noqa: S101 - narrowed by the layer above
    return DocumentRecord(
        document_id=document_id,
        document_type=classify_type(LAYER_REAL),
        # AD-007 / FR-003: every real specification is shared-library, not
        # duplicated per referencing project.
        project_id=SHARED_LIBRARY_PROJECT,
        # The manifest's own per-document licence identifier — `UFGS 01 33 00
        # (2021-02)` — is the document's name as its issuer states it. Nothing
        # is composed here from parts.
        title=entry.license_basis.document_identifier,
        source_kind=LAYER_REAL,
        license_basis=_canonical_license_basis(entry.license_basis.payload()),
        content_hash=entry.content_hash,
        path=document.path,
        source_ref=entry.source_location,
        issuing_body=entry.issuing_body,
        # `retrieved_at` is an RFC 3339 instant and `retrieval_date` is a date;
        # the date is the recorded instant's own, never today's.
        retrieval_date=date.fromisoformat(entry.retrieved_at[:10]),
    )


def _synthetic_record(document: CorpusDocument, document_id: str) -> DocumentRecord:
    entry = document.entry
    assert isinstance(entry, SyntheticEntry)  # noqa: S101 - narrowed by the layer above
    project_id = document.project_id
    if project_id is None:
        raise DocumentError(
            f"{document.location_id}: a SYNTHETIC manifest carries project_id and this one does not"
        )
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise DocumentError(
            f"{document.location_id}: project_id {project_id!r} does not match "
            f"{PROJECT_ID_PATTERN.pattern}"
        )
    if project_id == SHARED_LIBRARY_PROJECT:
        raise DocumentError(
            f"FR-003: {SHARED_LIBRARY_PROJECT} is reserved for the shared specification "
            f"library and must not be minted as an ordinary project ({document.location_id})"
        )
    return DocumentRecord(
        document_id=document_id,
        document_type=classify_type(LAYER_SYNTHETIC),
        project_id=project_id,
        # A generated transmittal has no issuer-stated name; its file stem is
        # the identity the generator gave it, and it is used verbatim rather
        # than a sentence being written about it.
        title=Path(document.entry.location).stem,
        source_kind=LAYER_SYNTHETIC,
        license_basis=_canonical_license_basis(entry.license_basis.payload()),
        content_hash=entry.content_hash,
        path=document.path,
        generator_id=entry.generator_id,
        generation_seed=str(entry.seed),
        generated_at=date.fromisoformat(entry.generation_date),
        # `plan.md` §Open Items: the four `generation_inputs` digests, in the
        # manifest's key order. `roster_hash` has its own column and is
        # deliberately not one of them.
        fixture_hashes=tuple(
            entry.generation_inputs[key] for key in sorted(entry.generation_inputs)
        ),
        roster_hash=entry.roster_hash,
    )


def build_document(document: CorpusDocument) -> DocumentRecord:
    """One `CorpusDocument` as the row it becomes (FR-002…FR-004, FR-006)."""
    if not isinstance(document, CorpusDocument):
        raise DocumentError(f"expected a CorpusDocument, found {type(document).__name__}")
    document_id = mint_document_id(document.stem, source=str(document.path))
    if document.layer == LAYER_REAL:
        return _real_record(document, document_id)
    if document.layer == LAYER_SYNTHETIC:
        return _synthetic_record(document, document_id)
    raise DocumentError(f"FR-006: no document type for layer {document.layer!r}")


def build_documents(documents: Iterable[CorpusDocument]) -> tuple[DocumentRecord, ...]:
    """Every document's record, with FR-052's collision check completed first.

    The check is **corpus-wide and precedes the first transaction**: minting
    runs over the whole enumeration, colliding identifiers are collected, and a
    collision raises naming every file that produced it. Returning the records
    only after that is what makes "a colliding run writes zero rows" a property
    of this function rather than a discipline the caller has to remember.
    """
    records: list[DocumentRecord] = []
    sources: defaultdict[str, list[str]] = defaultdict(list)
    for document in documents:
        record = build_document(document)
        records.append(record)
        sources[record.document_id].append(str(record.path))

    collisions = {document_id: paths for document_id, paths in sources.items() if len(paths) > 1}
    if collisions:
        detail = "; ".join(
            f"{document_id!r} minted from {sorted(paths)}"
            for document_id, paths in sorted(collisions.items())
        )
        raise DocumentError(f"FR-052: two corpus files yield the same derived identifier: {detail}")
    return tuple(records)
