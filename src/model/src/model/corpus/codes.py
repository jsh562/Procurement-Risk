"""Submittal descriptor codes, reviewer action letters, and field labels.

FR-023a. **Everything named here is a documented approximation of federal
transmittal practice, not a reproduction of any live form.** The current
revision of the government transmittal form returned 403 to automated
retrieval, so its code letters could not be verified against the source; an
unverified set presented as the real one would be a provenance claim this
project cannot support, which is precisely the failure Principle I exists to
prevent. The datasheet (`data/corpus/synthetic/datasheet.md`) repeats this
disclosure under `Stated Limits`, and validation asserts that it does (VR-052).

The approximation is deliberately *plausible* rather than *authentic*: the
descriptor series and the review-action letters are shaped like the ones a
coordinator would recognise, so downstream extraction exercises the same
structure, while nothing downstream may claim the corpus evidences the real
form's vocabulary.

**Two vocabularies, one of them committed.** The codes below live in code
because no committed artifact carries them — they are part of the generator's
source, like a layout template. The *field labels* are different: the injector
that mis-labels a field and the deriver that recovers the mis-labelling both
read `field-label-vocabulary.json`, so the labels must live in one committed
file, digested into every entry generated from it (FR-009b). This module reads
that file rather than restating it, following `sources.py`: a second
hand-written copy would be a second definition of the same data with nothing
comparing them.

Exactly one thing is stated in code *and* checked against the file:
`STRUCTURAL_FIELD_KEYS`. FR-023 fixes the six structural fields at requirement
level, so the vocabulary's own `structural_fields` list is **compared against**
this tuple rather than trusted — a restatement that is compared is a
cross-check, while one that is merely repeated is drift.

The vocabulary is read at import, as `sources.py` reads its policy: a malformed
or missing vocabulary is an import-time failure of every module that renders a
field label, which is the intent.

Stdlib only, following `model/roster/reader.py`: one error type, frozen
dataclasses, NFC at construction, results ordered deterministically.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from model.corpus.manifest import GENERATION_INPUT_PATHS
from model.corpus.paths import CorpusPathError, repository_relative_path

__all__ = [
    "ACTION_CODES",
    "APPROVING_AUTHORITIES",
    "DATE_FIELD_ORDER",
    "DESCRIPTOR_CODES",
    "FIELD_KEYS",
    "GOVERNMENT_APPROVAL_TAG",
    "STRUCTURAL_FIELD_KEYS",
    "VOCABULARY",
    "VOCABULARY_INPUT_PATH",
    "ActionCode",
    "CodesError",
    "DescriptorCode",
    "FieldLabels",
    "FieldLabelVocabulary",
    "action_code",
    "alternate_labels",
    "canonical_label",
    "descriptor_code",
    "fold_label",
    "load_vocabulary",
]

# The vocabulary's repository-relative path, taken from the closed three
# `manifest.py` holds rather than written out again — the string that names it
# here and the key recorded in every SYNTHETIC entry are then the same object.
VOCABULARY_INPUT_PATH = next(
    path for path in GENERATION_INPUT_PATHS if path.endswith("field-label-vocabulary.json")
)

# FR-023's six, in the order the requirement states them. Restated here and
# compared against the committed vocabulary's own list; see the module
# docstring for why this one restatement is admissible.
STRUCTURAL_FIELD_KEYS: tuple[str, ...] = (
    "transmittal_number",
    "specification_section",
    "descriptor_code",
    "approving_authority",
    "revision_suffix",
    "action_stamp",
)

# The `G` tag that marks an item requiring government approval. Approximated,
# like everything else here.
GOVERNMENT_APPROVAL_TAG = "G"


class CodesError(ValueError):
    """Raised when the committed field-label vocabulary is missing or malformed.

    One type for every failure, as `RosterError`, `ManifestError` and
    `RetrievalPolicyError` are: a caller learns the same thing from each of
    them — no document may be labelled from this vocabulary.
    """


@dataclass(frozen=True)
class DescriptorCode:
    """One submittal descriptor: its code and what the code stands for.

    `government_approval` records whether the descriptor is one this
    approximation marks with the `G` tag. It is a property of the descriptor
    rather than of a document so that a generator cannot tag two documents
    carrying one descriptor differently.
    """

    code: str
    title: str
    government_approval: bool = False

    @property
    def marker(self) -> str:
        """The descriptor as it is written on a transmittal, `G` tag included."""
        return f"{self.code}{GOVERNMENT_APPROVAL_TAG}" if self.government_approval else self.code


@dataclass(frozen=True)
class ActionCode:
    """One reviewer action: its letter, its meaning, and whether it closes.

    `closes_chain` is what makes a resubmittal chain constructible (FR-025):
    a chain runs from a non-closing action to a closing one, so the action code
    has to say which it is rather than leaving the generator to decide per
    document.
    """

    letter: str
    meaning: str
    closes_chain: bool


# ---------------------------------------------------------------------------
# The approximated code sets (FR-023a)
# ---------------------------------------------------------------------------

DESCRIPTOR_CODES: tuple[DescriptorCode, ...] = (
    DescriptorCode("SD-01", "Preconstruction Submittals"),
    DescriptorCode("SD-02", "Shop Drawings", government_approval=True),
    DescriptorCode("SD-03", "Product Data", government_approval=True),
    DescriptorCode("SD-04", "Samples"),
    DescriptorCode("SD-05", "Design Data", government_approval=True),
    DescriptorCode("SD-06", "Test Reports"),
    DescriptorCode("SD-07", "Certificates"),
    DescriptorCode("SD-08", "Manufacturer's Instructions"),
    DescriptorCode("SD-09", "Manufacturer's Field Reports"),
    DescriptorCode("SD-10", "Operation and Maintenance Data", government_approval=True),
    DescriptorCode("SD-11", "Closeout Submittals"),
)

ACTION_CODES: tuple[ActionCode, ...] = (
    ActionCode("A", "Approved", closes_chain=True),
    ActionCode("B", "Approved as Noted", closes_chain=True),
    ActionCode("C", "Revise and Resubmit", closes_chain=False),
    ActionCode("D", "Not Approved", closes_chain=False),
    ActionCode("E", "Receipt Acknowledged, No Action Taken", closes_chain=True),
)

# The approving-authority marker of FR-023. Two values, because the `G` tag
# only means something if some documents carry it and some do not.
APPROVING_AUTHORITIES: tuple[str, ...] = (
    "Government Approval Required (G)",
    "Contractor Approved",
)

_DESCRIPTORS_BY_CODE: Mapping[str, DescriptorCode] = MappingProxyType(
    {entry.code: entry for entry in DESCRIPTOR_CODES}
)
_ACTIONS_BY_LETTER: Mapping[str, ActionCode] = MappingProxyType(
    {entry.letter: entry for entry in ACTION_CODES}
)


def descriptor_code(code: str) -> DescriptorCode:
    """Look a descriptor up by code, failing on anything outside the closed set."""
    try:
        return _DESCRIPTORS_BY_CODE[code]
    except (KeyError, TypeError):
        raise CodesError(
            f"descriptor code {code!r} is not in the approximated set "
            f"{sorted(_DESCRIPTORS_BY_CODE)}"
        ) from None


def action_code(letter: str) -> ActionCode:
    """Look a reviewer action up by letter, failing on anything outside the set."""
    try:
        return _ACTIONS_BY_LETTER[letter]
    except (KeyError, TypeError):
        raise CodesError(
            f"reviewer action code {letter!r} is not in the approximated set "
            f"{sorted(_ACTIONS_BY_LETTER)}"
        ) from None


# ---------------------------------------------------------------------------
# The committed field-label vocabulary
# ---------------------------------------------------------------------------


def _fold(value: str) -> str:
    """The form two labels are compared in: NFC, case-folded, whitespace-collapsed.

    Disjointness is asserted on this form rather than on the literal strings.
    An alternate differing from a canonical label only in case or in run length
    of spaces is the *same* label to anything reading extracted text, so
    admitting the pair would make VR-035b undecidable while satisfying a naive
    string comparison.
    """
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def fold_label(value: str) -> str:
    """The public name of the folding above, for the deriver.

    `derive.py` has to recognise a label in extracted text, and it must do so in
    exactly the form disjointness was asserted in when the vocabulary was read.
    A second folding written there would be a second definition of "the same
    label", and the two could disagree about a pair this reader had already
    admitted.
    """
    if not isinstance(value, str):
        raise CodesError(f"a label is folded from a string, found {type(value).__name__}")
    return _fold(value)


def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise CodesError(f"{what} must be a string, found {type(value).__name__}")
    if not value.strip():
        raise CodesError(f"{what} must not be empty or whitespace-only")
    return unicodedata.normalize("NFC", value)


def _sequence(value: object, what: str) -> Sequence[object]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise CodesError(f"{what} must be an array, found {type(value).__name__}")
    if not value:
        raise CodesError(f"{what} must not be empty")
    return value


def _mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CodesError(f"{what} must be an object, found {type(value).__name__}")
    return value


@dataclass(frozen=True)
class FieldLabels:
    """One field's canonical label and the alternates that stand in for it.

    The alternates are what `INCONSISTENT_FIELD_LABEL` is injected from and
    what VR-035b derives it by. They are required non-empty: a field with no
    alternate could never carry the class, and a vocabulary that admitted one
    would let the layer satisfy FR-030 while some fields were unable to.
    """

    key: str
    canonical_label: str
    alternate_labels: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _text(self.key, "a field key"))
        object.__setattr__(
            self,
            "canonical_label",
            _text(self.canonical_label, f"fields[{self.key!r}].canonical_label"),
        )
        alternates = tuple(
            _text(value, f"fields[{self.key!r}].alternate_labels[{index}]")
            for index, value in enumerate(
                _sequence(self.alternate_labels, f"fields[{self.key!r}].alternate_labels")
            )
        )
        folded = [_fold(value) for value in alternates]
        if len(set(folded)) != len(folded):
            raise CodesError(
                f"fields[{self.key!r}].alternate_labels repeats a label: {list(alternates)}"
            )
        if _fold(self.canonical_label) in folded:
            raise CodesError(
                f"fields[{self.key!r}] lists its own canonical label "
                f"{self.canonical_label!r} as an alternate"
            )
        object.__setattr__(self, "alternate_labels", alternates)


@dataclass(frozen=True)
class FieldLabelVocabulary:
    """The parsed contents of `field-label-vocabulary.json`.

    Ordered or frozen throughout, so two readers of one file see one order —
    the same reason `discover_locations` sorts and `RetrievalPolicy` freezes.
    """

    fields: Mapping[str, FieldLabels]
    structural_field_keys: tuple[str, ...]
    date_field_order: tuple[str, ...]
    path: Path

    def labels(self, key: str) -> FieldLabels:
        try:
            return self.fields[key]
        except (KeyError, TypeError):
            raise CodesError(
                f"field {key!r} is not in the committed vocabulary {sorted(self.fields)}"
            ) from None

    @property
    def field_keys(self) -> tuple[str, ...]:
        """Every field key, ascending. Ordering is this module's, not the file's."""
        return tuple(sorted(self.fields))


def _load_document(path: Path) -> Mapping[str, object]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CodesError(f"cannot read the field-label vocabulary {path}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodesError(f"{path} is not valid UTF-8 JSON: {exc}") from exc
    return _mapping(payload, "the field-label vocabulary")


def load_vocabulary(path: Path | None = None, *, root: Path | None = None) -> FieldLabelVocabulary:
    """Read, validate, and return the committed field-label vocabulary.

    Every check below is a precondition of a rule that runs later. Alternates
    disjoint from **every** canonical label — not merely from their own field's
    — is what makes VR-035b decidable: a token that is one field's alternate
    and another's canonical label identifies no field, so the deriver could not
    say whether the document carried `INCONSISTENT_FIELD_LABEL` or was
    correctly labelled for a different field.
    """
    if path is not None:
        target = Path(path)
    else:
        try:
            target = repository_relative_path(VOCABULARY_INPUT_PATH, root)
        except CorpusPathError as exc:
            raise CodesError(f"cannot resolve the field-label vocabulary: {exc}") from exc

    document = _load_document(target)
    known = {"fields", "structural_fields", "date_field_order"}
    unexpected = sorted(set(document) - known)
    if unexpected:
        raise CodesError(f"{target} carries unexpected top-level keys {unexpected}")

    raw_fields = _mapping(document.get("fields"), "fields")
    if not raw_fields:
        raise CodesError("fields must not be empty")
    fields: dict[str, FieldLabels] = {}
    for key in sorted(raw_fields):
        record = _mapping(raw_fields[key], f"fields[{key!r}]")
        record_keys = sorted(set(record) - {"canonical_label", "alternate_labels"})
        if record_keys:
            raise CodesError(f"fields[{key!r}] carries unexpected keys {record_keys}")
        fields[key] = FieldLabels(
            key=key,
            canonical_label=record.get("canonical_label"),
            alternate_labels=record.get("alternate_labels", ()),
        )

    canonical = {_fold(entry.canonical_label): entry.key for entry in fields.values()}
    if len(canonical) != len(fields):
        raise CodesError("two fields share one canonical_label; a shared label identifies no field")
    seen_alternates: dict[str, str] = {}
    for entry in fields.values():
        for alternate in entry.alternate_labels:
            folded = _fold(alternate)
            if folded in canonical:
                raise CodesError(
                    f"fields[{entry.key!r}] lists {alternate!r} as an alternate, but it is the "
                    f"canonical label of {canonical[folded]!r}; alternate_labels must be disjoint "
                    "from every canonical_label"
                )
            if folded in seen_alternates:
                raise CodesError(
                    f"{alternate!r} is an alternate of both {seen_alternates[folded]!r} and "
                    f"{entry.key!r}; an alternate must identify exactly one field"
                )
            seen_alternates[folded] = entry.key

    raw_structural = _sequence(document.get("structural_fields"), "structural_fields")
    structural = tuple(
        _text(value, f"structural_fields[{index}]") for index, value in enumerate(raw_structural)
    )
    if structural != STRUCTURAL_FIELD_KEYS:
        raise CodesError(
            f"structural_fields must be exactly FR-023's six, in order: "
            f"{list(STRUCTURAL_FIELD_KEYS)}; found {list(structural)}"
        )

    raw_dates = _sequence(document.get("date_field_order"), "date_field_order")
    dates = tuple(
        _text(value, f"date_field_order[{index}]") for index, value in enumerate(raw_dates)
    )
    if len(set(dates)) != len(dates):
        raise CodesError(f"date_field_order repeats a field: {list(dates)}")
    if len(dates) < 2:
        raise CodesError(
            "date_field_order must name at least two fields; OUT_OF_ORDER_DATE is a relation "
            "between dates and is undecidable over fewer"
        )

    for key in (*structural, *dates):
        if key not in fields:
            raise CodesError(f"{key!r} is named but has no entry under fields")

    return FieldLabelVocabulary(
        fields=MappingProxyType(fields),
        structural_field_keys=structural,
        date_field_order=dates,
        path=target,
    )


VOCABULARY: FieldLabelVocabulary = load_vocabulary()

# The committed vocabulary, exposed rather than restated.
FIELD_KEYS: tuple[str, ...] = VOCABULARY.field_keys
DATE_FIELD_ORDER: tuple[str, ...] = VOCABULARY.date_field_order


def canonical_label(key: str) -> str:
    """The label a correctly-labelled document writes for `key`."""
    return VOCABULARY.labels(key).canonical_label


def alternate_labels(key: str) -> tuple[str, ...]:
    """The labels an `INCONSISTENT_FIELD_LABEL` injection may substitute."""
    return VOCABULARY.labels(key).alternate_labels
