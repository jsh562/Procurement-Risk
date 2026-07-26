"""Manifest entries, the canonical manifest writer, and the digest helpers.

FR-006b / FR-007 / MS-1…MS-6. A corpus location's `manifest.json` is the only
record of where its documents came from, so this module owns three things and
keeps them separable: the *shape* of an entry (which fields exist, per layer),
the *form* of every recorded value (patterns, closed sets, digest surface), and
the *serialization* that makes SC-012's byte-identity claim achievable.

**The layer asymmetry is a property of the type, not a convention.** A REAL
entry carries the eight retrieval fields of FR-008; a SYNTHETIC entry carries
the seven generation fields of FR-009. They are two classes, so a synthetic
entry has no attribute slot for `source_location` at all and cannot be
constructed carrying one — the prohibition of VR-017 / VR-027 is enforced by
`__init__` before any schema or validator sees the file. A fabricated issuing
body is indistinguishable downstream from a verified one, which is the failure
Principle I exists to prevent, so "carries a blank" is not an option here.

**The five digest kinds stay distinct** (`data-model.md` §The Digest Kinds).
They are computed over five different things and must never be substituted for
one another, so each has its own named helper below rather than one generic
`sha256()` that a caller can point at anything:

- `content_hash_of_file` — the committed file's raw bytes
- `upstream_digest_of_response` — the bytes as retrieved, REAL only
- `generation_input_digests` — each generation input's raw bytes, SYNTHETIC only
- `roster_digest` — the E001 reader's canonical-content value, consumed
  verbatim and **never recomputed** here, SYNTHETIC only
- `document_model_hash` — `model.corpus.model`'s, over the pre-render model

The first three are over bytes; `roster_digest` is over content re-serialized
canonically. Reformatting the roster file moves nothing while reformatting a
corpus PDF moves everything, and a reader who assumes the two are the same kind
of digest will draw the wrong conclusion from a match.

**MS-5, clock-free by construction**: nothing in this module reads a clock.
`generation_date` is a committed constant and `retrieved_at` is a historical
constant recorded once, so re-running the writer is a no-op on the file system.
`datetime` appears only to parse a recorded string, never to produce one — the
"not in the future" half of VR-020 needs a clock and is therefore the
validator's, not this module's.

Stdlib only, following `model/roster/reader.py`: one error type, NFC at
construction, frozen dataclasses, `sha256:` plus 64 lowercase hex.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlparse

from model.corpus.paths import LOCATION_ID_PATTERN, MANIFEST_FILENAME
from model.roster.reader import Roster

__all__ = [
    "COMMON_FIELDS",
    "GENERATION_INPUT_PATHS",
    "IRREGULARITY_CLASSES",
    "LAYER_REAL",
    "LAYER_SYNTHETIC",
    "REAL_ONLY_FIELDS",
    "SYNTHETIC_ONLY_FIELDS",
    "Manifest",
    "ManifestEntry",
    "ManifestError",
    "RealEntry",
    "RealLicenseBasis",
    "SyntheticEntry",
    "SyntheticLicenseBasis",
    "canonical_manifest_bytes",
    "content_hash_of_file",
    "generation_input_digests",
    "roster_digest",
    "sha256_of_bytes",
    "sha256_of_file",
    "upstream_digest_of_response",
    "write_manifest",
]

LAYER_REAL = "REAL"
LAYER_SYNTHETIC = "SYNTHETIC"

# The applicable field sets, exported because VR-017 and VR-027 assert the
# prohibition in both directions and must name the same two lists this module
# builds its two classes from — a second hand-written copy in the validator
# would be a second definition of the asymmetry.
COMMON_FIELDS = frozenset({"location", "layer", "license_basis", "content_hash"})
REAL_ONLY_FIELDS = frozenset(
    {
        "source_location",
        "retrieval_response_status",
        "retrieved_at",
        "issuing_body",
        "masterformat_section",
        "agency_variant",
        "revision_date",
        "upstream_digest",
    }
)
SYNTHETIC_ONLY_FIELDS = frozenset(
    {
        "generator_id",
        "seed",
        "generation_date",
        "roster_hash",
        "generation_inputs",
        "document_model_hash",
        "irregularity_classes",
    }
)

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
# A single filename: no separator, no `..`, not absolute. The pattern admits
# neither `/` nor `\` nor `:`, so a POSIX path, a Windows path, a drive-letter
# prefix and a UNC prefix all fail here rather than at a later resolution step.
LOCATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.pdf$")
MASTERFORMAT_PATTERN = re.compile(r"^[0-9]{2} [0-9]{2} [0-9]{2}$")
# Month precision, which is the precision UFGS publishes at.
REVISION_DATE_PATTERN = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
GENERATION_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
# RFC 3339, UTC, `Z` suffix — a numeric offset is rejected rather than
# normalized, so two entries cannot record one instant in two forms.
RETRIEVED_AT_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$"
)

REAL_BASIS_ID = "us-gov-17usc105-ufgs"
SYNTHETIC_BASIS_ID = "project-generated-no-third-party-rights"
BASIS_IDS = frozenset({REAL_BASIS_ID, SYNTHETIC_BASIS_ID})
# Closed, not free text: "non-empty" alone would admit a citation conferring no
# public-domain status while satisfying every stated rule. Extending the closed
# basis set is what extends this one, in the same change.
STATUTE_FOR_BASIS: Mapping[str, str] = MappingProxyType({REAL_BASIS_ID: "17 U.S.C. §105(a)"})
POINT_OF_USE_CHECK = "NO_COPYRIGHTED_EXCERPT_FOUND"
THIRD_PARTY_RIGHTS = "NONE"

# VR-061's closed three, repository-relative. Held here rather than in the
# generator because the keys are path-valued and externally controlled once
# they are written into a manifest (CWE-73): membership is decided against this
# literal set before any filesystem access, so a traversal sequence in a
# manifest-supplied key never reaches a resolution step. The roster is the
# fourth generation input and is deliberately not a key — its digest is
# `roster_hash`, because the reader's value is over canonical content.
GENERATION_INPUT_PATHS: tuple[str, ...] = (
    "data/corpus/synthetic/equipment-category-map.json",
    "data/corpus/synthetic/field-label-vocabulary.json",
    "data/corpus/synthetic/generation-config.json",
)

# The closed five of FR-030, in ascending codepoint order. `irregularity.py`
# (T048) builds its enum against this tuple rather than restating it, so the
# recorded vocabulary and the injector's vocabulary cannot drift apart.
IRREGULARITY_CLASSES: tuple[str, ...] = (
    "INCONSISTENT_FIELD_LABEL",
    "MISSING_OR_BLANK_FIELD",
    "OUT_OF_ORDER_DATE",
    "PAGE_SPLIT_FIELD",
    "SCAN_DEGRADATION",
)

_CHUNK = 1 << 20


class ManifestError(ValueError):
    """Raised when an entry or a manifest is malformed.

    One type for every failure, as `RosterError` and `DocumentModelError` are:
    a caller learns the same thing from each of them — this manifest must not
    be written, and nothing may be recorded from it.
    """


# ---------------------------------------------------------------------------
# The five digest kinds, each with its own name
# ---------------------------------------------------------------------------


def sha256_of_bytes(raw: bytes) -> str:
    """The one primitive: ``sha256:`` followed by 64 lowercase hex characters.

    The same surface form `read_roster()` and `document_model_hash()` emit, so
    a downstream comparison can treat all five kinds as the same kind of string
    even though they are digests over different things (FR-007, VR-016).
    """
    if not isinstance(raw, bytes | bytearray):
        raise ManifestError(f"a digest is taken over bytes, found {type(raw).__name__}")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def sha256_of_file(path: Path) -> str:
    """Digest a file's **raw bytes** — no parsing, no canonicalization.

    Read in chunks rather than whole: the corpus holds PDFs, and the retrieval
    client admits bodies up to 50 MB.
    """
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
    except OSError as exc:
        raise ManifestError(f"cannot digest {path}: {exc}") from exc
    return "sha256:" + digest.hexdigest()


def content_hash_of_file(path: Path) -> str:
    """`content_hash` — over the committed file's bytes, exactly as on disk.

    Recomputed by the validator on every run (VR-012) and never trusted as
    evidence of itself. Distinct from `upstream_digest`, which is over the
    bytes *as retrieved*; FR-008a requires the two equal for a REAL entry, and
    that redundancy is the whole check.
    """
    return sha256_of_file(path)


def upstream_digest_of_response(body: bytes) -> str:
    """`upstream_digest` — over the response body, REAL only (FR-008c).

    Taken from the bytes in hand at retrieval, **before** the file is written,
    never re-read from the committed file afterwards. Nothing offline can tell
    a digest recorded here from one back-filled out of the committed file, so
    this function existing separately from `content_hash_of_file` is the only
    place the distinction is expressed in code.
    """
    return sha256_of_bytes(body)


def generation_input_digests(repo_root: Path) -> Mapping[str, str]:
    """`generation_inputs` — raw-byte digests of the closed three (FR-009b).

    Keys are the repository-relative paths of `GENERATION_INPUT_PATHS` and are
    validator-owned literals rather than caller-supplied strings, so no path
    here originates outside this module. A missing input is an error, never an
    omitted key: an entry recording two of three inputs would pass every
    per-key rule while leaving one input undigested.
    """
    root = Path(repo_root)
    digests = {}
    for relative in GENERATION_INPUT_PATHS:
        target = root.joinpath(*relative.split("/"))
        if not target.is_file():
            raise ManifestError(f"generation input missing: {relative} (looked in {root})")
        digests[relative] = sha256_of_file(target)
    return MappingProxyType(digests)


def roster_digest(roster: Roster) -> str:
    """`roster_hash` — E001's value, consumed verbatim (FR-020, VR-029).

    Deliberately not a computation. `Roster.content_hash` is a digest over the
    roster's canonical **re-serialized content**, not over the roster file's
    bytes, so recomputing it here with `sha256_of_file` would record a
    different number under the same name and make VR-029's comparison against a
    live `read_roster()` fail for a formatting change that is not drift.
    """
    if not isinstance(roster, Roster):
        raise ManifestError(f"roster_hash comes from a Roster, found {type(roster).__name__}")
    return _digest(roster.content_hash, "roster_hash")


# ---------------------------------------------------------------------------
# Field-level checks
# ---------------------------------------------------------------------------


def _nfc(value: str) -> str:
    """VR-068, applied at construction rather than at file lookup.

    Normalizing here means no un-normalized entry can exist, so `location`
    values are compared, sorted and matched against directory entries in one
    normal form. MS-1's codepoint ordering already assumes it.
    """
    return unicodedata.normalize("NFC", value)


def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{what} must be a string, found {type(value).__name__}")
    if not value.strip():
        raise ManifestError(f"{what} must not be empty or whitespace-only")
    return _nfc(value)


def _matching(value: object, pattern: re.Pattern[str], what: str) -> str:
    text = _text(value, what)
    if not pattern.fullmatch(text):
        raise ManifestError(f"{what} must match {pattern.pattern}, found {text!r}")
    return text


def _digest(value: object, what: str) -> str:
    """Every digest field, in the one surface form. Uppercase hex fails."""
    return _matching(value, DIGEST_PATTERN, what)


def _one_of(value: object, admissible: frozenset[str] | tuple[str, ...], what: str) -> str:
    text = _text(value, what)
    if text not in admissible:
        raise ManifestError(f"{what} must be one of {sorted(admissible)}, found {text!r}")
    return text


def _location(value: object) -> str:
    location = _text(value, "location")
    if ".." in location:
        raise ManifestError(f"location must contain no '..' segment, found {location!r}")
    if not LOCATION_PATTERN.fullmatch(location):
        raise ManifestError(
            f"location must be a single .pdf filename with no path separator, found {location!r}"
        )
    return location


def _integer(value: object, what: str) -> int:
    # `bool` is an `int` in Python and is excluded deliberately: a boolean seed
    # or status code is a caller mistake that would otherwise serialize as
    # `true` and pass every numeric rule.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ManifestError(f"{what} must be an integer, found {type(value).__name__}")
    return value


def _calendar_date(value: str, what: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ManifestError(f"{what} is not a real calendar date: {value!r} ({exc})") from exc
    return value


# ---------------------------------------------------------------------------
# License basis — one shape per layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RealLicenseBasis:
    """FR-011 / FR-012a. Four components, three of them closed sets.

    `document_identifier` is the per-document component and is expected to
    differ within a location; `basis_id` is the governing component FR-013
    compares, which is why the no-mixed-licenses rule is expressed over it
    alone (`data-model.md` §License Basis).
    """

    document_identifier: str
    basis_id: str = REAL_BASIS_ID
    statute: str = STATUTE_FOR_BASIS[REAL_BASIS_ID]
    point_of_use_check: str = POINT_OF_USE_CHECK

    def __post_init__(self) -> None:
        basis_id = _one_of(self.basis_id, frozenset({REAL_BASIS_ID}), "license_basis.basis_id")
        object.__setattr__(self, "basis_id", basis_id)
        object.__setattr__(
            self,
            "document_identifier",
            _text(self.document_identifier, "license_basis.document_identifier"),
        )
        statute = _text(self.statute, "license_basis.statute")
        if statute != STATUTE_FOR_BASIS[basis_id]:
            raise ManifestError(
                f"license_basis.statute {statute!r} does not agree with basis_id {basis_id!r}; "
                f"expected {STATUTE_FOR_BASIS[basis_id]!r}"
            )
        object.__setattr__(self, "statute", statute)
        object.__setattr__(
            self,
            "point_of_use_check",
            _one_of(
                self.point_of_use_check,
                frozenset({POINT_OF_USE_CHECK}),
                "license_basis.point_of_use_check",
            ),
        )

    def payload(self) -> dict[str, object]:
        return {
            "basis_id": self.basis_id,
            "statute": self.statute,
            "document_identifier": self.document_identifier,
            "point_of_use_check": self.point_of_use_check,
        }


@dataclass(frozen=True)
class SyntheticLicenseBasis:
    """FR-012 / FR-012a. Two `const` assertions plus a required statement.

    The free-text statement alone would assert the claim with nothing able to
    test it; the two constants are the machine-checkable half.
    """

    statement: str
    basis_id: str = SYNTHETIC_BASIS_ID
    generated_by_this_project: bool = True
    third_party_rights: str = THIRD_PARTY_RIGHTS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "basis_id",
            _one_of(self.basis_id, frozenset({SYNTHETIC_BASIS_ID}), "license_basis.basis_id"),
        )
        object.__setattr__(self, "statement", _text(self.statement, "license_basis.statement"))
        if self.generated_by_this_project is not True:
            raise ManifestError("license_basis.generated_by_this_project must be true")
        object.__setattr__(
            self,
            "third_party_rights",
            _one_of(
                self.third_party_rights,
                frozenset({THIRD_PARTY_RIGHTS}),
                "license_basis.third_party_rights",
            ),
        )

    def payload(self) -> dict[str, object]:
        return {
            "basis_id": self.basis_id,
            "generated_by_this_project": self.generated_by_this_project,
            "third_party_rights": self.third_party_rights,
            "statement": self.statement,
        }


LicenseBasis = RealLicenseBasis | SyntheticLicenseBasis


# ---------------------------------------------------------------------------
# Entries — two classes, because the asymmetry is the point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestEntry(ABC):
    """The four common fields of FR-007, and nothing else.

    Abstract: an entry with no layer is not a thing that can exist, and `layer`
    is a property of the class rather than a constructor argument, so no caller
    can label a retrieval-shaped entry `SYNTHETIC`.
    """

    location: str
    license_basis: LicenseBasis
    content_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "location", _location(self.location))
        object.__setattr__(self, "content_hash", _digest(self.content_hash, "content_hash"))
        if not isinstance(self.license_basis, RealLicenseBasis | SyntheticLicenseBasis):
            raise ManifestError(
                "license_basis must be a RealLicenseBasis or a SyntheticLicenseBasis, "
                f"found {type(self.license_basis).__name__}"
            )

    @property
    @abstractmethod
    def layer(self) -> str:
        """`REAL` or `SYNTHETIC`, fixed by the class."""

    @abstractmethod
    def _layer_payload(self) -> dict[str, object]:
        """This layer's own fields, and only this layer's."""

    def payload(self) -> dict[str, object]:
        return {
            "location": self.location,
            "layer": self.layer,
            "license_basis": self.license_basis.payload(),
            "content_hash": self.content_hash,
            **self._layer_payload(),
        }


@dataclass(frozen=True)
class RealEntry(ManifestEntry):
    """A retrieved document: the four common fields plus FR-008's eight.

    There is no slot here for `generator_id`, `seed`, `generation_date`,
    `roster_hash`, `generation_inputs`, `document_model_hash`, or
    `irregularity_classes` — VR-017's prohibited list, enforced by the absence
    of the attributes rather than by a check that could be removed.
    """

    license_basis: RealLicenseBasis
    source_location: str
    retrieval_response_status: int
    retrieved_at: str
    issuing_body: str
    masterformat_section: str
    agency_variant: str
    revision_date: str
    upstream_digest: str

    @property
    def layer(self) -> str:
        return LAYER_REAL

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.license_basis, RealLicenseBasis):
            raise ManifestError(
                f"a REAL entry carries a RealLicenseBasis, "
                f"found {type(self.license_basis).__name__}"
            )

        url = _text(self.source_location, "source_location")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ManifestError(f"source_location must be an absolute https URL, found {url!r}")
        object.__setattr__(self, "source_location", url)

        # Membership of the host allow-list is VR-022's and needs
        # `retrieval-policy.json`, which this module deliberately does not read.
        status = _integer(self.retrieval_response_status, "retrieval_response_status")
        if not 100 <= status <= 599:
            raise ManifestError(f"retrieval_response_status must be an HTTP status, found {status}")
        object.__setattr__(self, "retrieval_response_status", status)

        retrieved_at = _matching(self.retrieved_at, RETRIEVED_AT_PATTERN, "retrieved_at")
        try:
            datetime.fromisoformat(retrieved_at)
        except ValueError as exc:
            raise ManifestError(f"retrieved_at is not a real instant: {retrieved_at!r}") from exc
        object.__setattr__(self, "retrieved_at", retrieved_at)

        object.__setattr__(self, "issuing_body", _text(self.issuing_body, "issuing_body"))
        object.__setattr__(
            self,
            "masterformat_section",
            _matching(self.masterformat_section, MASTERFORMAT_PATTERN, "masterformat_section"),
        )
        object.__setattr__(self, "agency_variant", _text(self.agency_variant, "agency_variant"))
        object.__setattr__(
            self,
            "revision_date",
            _matching(self.revision_date, REVISION_DATE_PATTERN, "revision_date"),
        )
        object.__setattr__(
            self, "upstream_digest", _digest(self.upstream_digest, "upstream_digest")
        )

    def _layer_payload(self) -> dict[str, object]:
        return {
            "source_location": self.source_location,
            "retrieval_response_status": self.retrieval_response_status,
            "retrieved_at": self.retrieved_at,
            "issuing_body": self.issuing_body,
            "masterformat_section": self.masterformat_section,
            "agency_variant": self.agency_variant,
            "revision_date": self.revision_date,
            "upstream_digest": self.upstream_digest,
        }


@dataclass(frozen=True)
class SyntheticEntry(ManifestEntry):
    """A generated document: the four common fields plus FR-009's seven.

    There is no slot here for any of the eight retrieval fields — VR-027's
    prohibited list. A generated document must not carry retrieval provenance
    it does not have (`project-instructions.md` §Data Provenance), and the way
    to guarantee that is to make the field unconstructible rather than to
    require every writer to leave it out.
    """

    license_basis: SyntheticLicenseBasis
    generator_id: str
    seed: int
    generation_date: str
    roster_hash: str
    generation_inputs: Mapping[str, str]
    document_model_hash: str
    irregularity_classes: tuple[str, ...] = ()

    @property
    def layer(self) -> str:
        return LAYER_SYNTHETIC

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.license_basis, SyntheticLicenseBasis):
            raise ManifestError(
                f"a SYNTHETIC entry carries a SyntheticLicenseBasis, "
                f"found {type(self.license_basis).__name__}"
            )

        object.__setattr__(self, "generator_id", _text(self.generator_id, "generator_id"))
        object.__setattr__(self, "seed", _integer(self.seed, "seed"))

        generation_date = _matching(
            self.generation_date, GENERATION_DATE_PATTERN, "generation_date"
        )
        object.__setattr__(
            self, "generation_date", _calendar_date(generation_date, "generation_date")
        )

        object.__setattr__(self, "roster_hash", _digest(self.roster_hash, "roster_hash"))
        object.__setattr__(
            self,
            "document_model_hash",
            _digest(self.document_model_hash, "document_model_hash"),
        )

        if not isinstance(self.generation_inputs, Mapping):
            raise ManifestError(
                f"generation_inputs must be a mapping, "
                f"found {type(self.generation_inputs).__name__}"
            )
        supplied = {str(key): value for key, value in self.generation_inputs.items()}
        if set(supplied) != set(GENERATION_INPUT_PATHS):
            unexpected = sorted(set(supplied) - set(GENERATION_INPUT_PATHS))
            missing = sorted(set(GENERATION_INPUT_PATHS) - set(supplied))
            raise ManifestError(
                f"generation_inputs keys wrong; unexpected={unexpected} missing={missing}"
            )
        object.__setattr__(
            self,
            "generation_inputs",
            MappingProxyType(
                {key: _digest(supplied[key], f"generation_inputs[{key!r}]") for key in supplied}
            ),
        )

        classes = self.irregularity_classes
        if isinstance(classes, str) or not isinstance(classes, Sequence):
            raise ManifestError(
                f"irregularity_classes must be a sequence, found {type(classes).__name__}"
            )
        for value in classes:
            _one_of(value, IRREGULARITY_CLASSES, "an irregularity class")
        # MS-2: deduplicated and sorted ascending at construction, so no caller
        # can record the same content in two orders. May be empty — a clean
        # document is a real outcome, not a missing value.
        object.__setattr__(self, "irregularity_classes", tuple(sorted(set(classes))))

    def _layer_payload(self) -> dict[str, object]:
        return {
            "generator_id": self.generator_id,
            "seed": self.seed,
            "generation_date": self.generation_date,
            "roster_hash": self.roster_hash,
            "generation_inputs": dict(self.generation_inputs),
            "document_model_hash": self.document_model_hash,
            "irregularity_classes": list(self.irregularity_classes),
        }


# ---------------------------------------------------------------------------
# The manifest and its canonical serialization (MS-1…MS-6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Manifest:
    """One corpus location's manifest.

    `layer` is **derived** from the entries rather than declared beside them:
    VR-007 requires every entry's layer to equal the manifest's, and a manifest
    holding two kinds of entry is rejected at construction instead of being
    written and caught later. `project_id` is present exactly when the layer is
    SYNTHETIC and equals the final segment of `location_id` (VR-006).
    """

    location_id: str
    entries: tuple[ManifestEntry, ...]
    project_id: str | None = None

    def __post_init__(self) -> None:
        location_id = _matching(self.location_id, LOCATION_ID_PATTERN, "location_id")
        object.__setattr__(self, "location_id", location_id)

        entries = tuple(self.entries)
        if not entries:
            # VR-066's non-vacuity, at the writer: a manifest over zero entries
            # satisfies every "for each entry" rule by having nothing to fail.
            raise ManifestError(f"{location_id}: a manifest must carry at least one entry")
        for index, entry in enumerate(entries):
            if not isinstance(entry, ManifestEntry):
                raise ManifestError(
                    f"entries[{index}] must be a ManifestEntry, found {type(entry).__name__}"
                )
        layers = {entry.layer for entry in entries}
        if len(layers) != 1:
            raise ManifestError(
                f"{location_id}: a corpus location holds exactly one layer, found {sorted(layers)}"
            )

        _reject_duplicate_locations(location_id, entries)
        _reject_case_collisions(location_id, entries)
        # MS-1 held from construction rather than only at serialization, so the
        # ordering is a property of the object every reader sees.
        object.__setattr__(self, "entries", tuple(sorted(entries, key=lambda e: e.location)))

        layer = layers.pop()
        if layer == LAYER_SYNTHETIC:
            project_id = _text(self.project_id, "project_id")
            final_segment = location_id.rsplit("/", 1)[-1]
            if project_id != final_segment:
                raise ManifestError(
                    f"project_id {project_id!r} must equal the final segment of "
                    f"location_id {location_id!r}"
                )
            object.__setattr__(self, "project_id", project_id)
        elif self.project_id is not None:
            raise ManifestError(
                f"{location_id}: project_id is present only on a SYNTHETIC manifest, "
                f"found {self.project_id!r}"
            )

    @property
    def layer(self) -> str:
        return self.entries[0].layer

    def payload(self) -> dict[str, object]:
        """The JSON object MS-3 serializes.

        Top-level keys are exactly `{location_id, layer, entries}` plus
        `project_id` when SYNTHETIC — no `version`, `revision`, `generated_at`
        or `updated` field is written (VR-058). A `generated_at` would be a
        hand-maintained marker that records drift without detecting it, and
        would additionally break VR-042's byte comparison on every run.
        """
        payload: dict[str, object] = {
            "location_id": self.location_id,
            "layer": self.layer,
            "entries": [entry.payload() for entry in self.entries],
        }
        if self.project_id is not None:
            payload["project_id"] = self.project_id
        return payload


def _reject_duplicate_locations(location_id: str, entries: Sequence[ManifestEntry]) -> None:
    """`location` is the entry's key within a manifest (VR-011)."""
    counts = Counter(entry.location for entry in entries)
    duplicates = sorted(location for location, count in counts.items() if count > 1)
    if duplicates:
        raise ManifestError(
            f"{location_id}: location values must be unique within a manifest; "
            f"repeated: {duplicates}"
        )


def _reject_case_collisions(location_id: str, entries: Sequence[ManifestEntry]) -> None:
    """VR-068's second half, decided here rather than by the filesystem.

    Two codepoint-distinct `location` values that fold to one string name one
    file on a case-folding filesystem and two on a case-sensitive one, which
    makes VR-011's entry↔file bijection pass on one platform and fail on
    another over identical committed content.
    """
    folded: dict[str, list[str]] = {}
    for entry in entries:
        folded.setdefault(entry.location.casefold(), []).append(entry.location)
    collisions = {
        key: sorted(set(values)) for key, values in folded.items() if len(set(values)) > 1
    }
    if collisions:
        detail = "; ".join(
            f"{sorted(values)} fold to {key!r}" for key, values in collisions.items()
        )
        raise ManifestError(
            f"{location_id}: location values must not collide under case folding: {detail}"
        )


def canonical_manifest_bytes(manifest: Manifest) -> bytes:
    """Serialize a manifest for writing and for byte comparison (MS-1…MS-3).

    `indent=2` and `sort_keys=True` so key order and whitespace are properties
    of the writer rather than of the caller; `ensure_ascii=False` so non-ASCII
    is preserved rather than escaped; exactly one trailing `\\n`. Entries are
    already sorted by `location` and `irregularity_classes` already
    deduplicated and sorted, both at construction.

    The invariant this exists to hold: two manifests with equal content
    serialize to byte-identical files whatever order their entries and keys
    were supplied in, and two differing in any recorded value serialize to
    different bytes (PB-5).
    """
    if not isinstance(manifest, Manifest):
        raise ManifestError(f"expected a Manifest, found {type(manifest).__name__}")
    text = json.dumps(manifest.payload(), indent=2, sort_keys=True, ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def write_manifest(location_dir: Path, manifest: Manifest) -> Path:
    """Write `manifest.json` into a corpus location directory (MS-4).

    Written with `Path.write_bytes` and never in text mode. This is
    load-bearing rather than stylistic: the development machine is Windows,
    `core.autocrlf` rewrites the working copy, and a default text-mode write
    would emit CRLF and fail VR-042's byte comparison for a line-ending reason
    unrelated to content (HINT-004). The **platform of record** for every
    byte-identity claim is the Linux verification runner.

    The directory's final segment must match the manifest's `location_id`,
    because a manifest written beside the wrong documents satisfies every rule
    inside itself and fails VR-004 much later.

    MS-6 is a procedure this function cannot enforce: the real manifest is
    written once at retrieval, and regeneration rewrites only the five
    synthetic manifests.
    """
    directory = Path(location_dir)
    expected = manifest.location_id.rsplit("/", 1)[-1]
    if directory.name != expected:
        raise ManifestError(
            f"location_id {manifest.location_id!r} does not match the directory it is "
            f"written into: expected a directory named {expected!r}, found {directory.name!r}"
        )
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / MANIFEST_FILENAME
        target.write_bytes(canonical_manifest_bytes(manifest))
    except OSError as exc:
        raise ManifestError(f"cannot write manifest into {directory}: {exc}") from exc
    return target
