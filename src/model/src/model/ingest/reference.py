"""The reference set: the generator's pre-render document model, digest-verified.

FR-067, SC-052. Every accuracy figure this epic publishes takes its **expected**
side from here and from nowhere else.

**Why not the parsed text.** The obvious reference is what this epic's own
parser read off the page — and it is the one thing that must not be used. A
figure scored against the run's own parse is a derived oracle against itself: a
chunker that dropped a line would score perfectly, because the expected side
dropped it too. The same objection rules out the chunk a value came from and any
other artifact of the run being measured. The reference is the document model
the generator composed **before rendering**, which exists independently of every
step being measured.

**Reproduced, then verified — in that order, and before any figure is computed.**
The model is rebuilt here from the committed generation inputs (the roster, the
configuration, the manufacturer catalogue, the field-label vocabulary) through
the generator's own `compose_layer`, which writes nothing. Its digest is then
required equal to the `document_model_hash` each synthetic manifest entry
carries. A mismatch aborts before a single figure exists, because a figure
computed against an unverified reference is a figure whose expected side nobody
can vouch for — and it would look exactly like a correct one.

**This module is not the baseline, and the distinction is load-bearing.** The
baseline extractor may not read the generator's templates, renderer, or
pre-render model — that is the answer key, and an opponent reading it cannot
lose (FR-050, AD-012, and the committed import contract that enforces it). The
*reference set* is the answer key, deliberately: it is the expected side of the
comparison, not a competitor in it. `model.ingest.baseline` must therefore never
import this module, and the contract on it is what stops the two from meeting.

**The real layer has no reference.** The 26 specifications were retrieved, not
generated, so no pre-render model exists for them and none can be manufactured.
FR-060 publishes that layer as *not measured*, with this as the reason — not as
a zero and not as an empty denominator.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from model.corpus.generate import compose_layer
from model.corpus.manifest import LAYER_SYNTHETIC, SyntheticEntry
from model.ingest.documents import mint_document_id
from model.ingest.manifest_reader import iter_entries

__all__ = [
    "REAL_LAYER_NOT_MEASURED",
    "VOCABULARY_BY_GENERATOR_KEY",
    "ReferenceDocument",
    "ReferenceField",
    "ReferenceSet",
    "ReferenceSetError",
    "build_reference_set",
]


class ReferenceSetError(RuntimeError):
    """The reference set cannot be built, or cannot be vouched for.

    One type, and every one of them stops the run before a figure exists. That
    is the requirement's own ordering: "MUST verify it is the committed one
    **before any figure is computed**", so there is no partial mode in which
    some figures are published against a reference that failed verification.
    """


#: FR-060 / FR-067's stated reason for the layer that has no reference. Held as
#: a constant so the report's words and this module's refusal are the same
#: sentence rather than two paraphrases.
REAL_LAYER_NOT_MEASURED = (
    "The 26 real specifications were retrieved, not generated, so no pre-render document "
    "model exists for them and none can be reconstructed. FR-067 forbids scoring against "
    "the chunk text a value was read out of or against this epic's own parse, which are "
    "the only other candidates — so the real layer is published as not measured, with "
    "this reason, rather than as a zero or an empty denominator (SC-047)."
)

#: The generator's field keys mapped onto E003's seeded vocabulary terms.
#:
#: The two vocabularies are genuinely different things and the mapping is
#: declared rather than derived: the generator names what a *layout* prints
#: (`material_item`, `date_returned`), and `field_vocabulary` names what the
#: *schema* stores (`product_description`, `approval_date`). A generator key
#: absent from this mapping is a printed field with no storable term — the
#: transmittal's contract number, project identifier, vendor name, descriptor
#: code, approving authority, revision suffix and receipt date are all of them —
#: and it is deliberately absent rather than mapped onto the nearest term.
#:
#: Recall is denominated on "the fields the generator recorded as printed"
#: (FR-060), which means the printed fields **that have a vocabulary term**:
#: nothing can be stored for a field the schema has no name for, so counting one
#: as a miss would denominate recall on a population extraction could not have
#: reached.
VOCABULARY_BY_GENERATOR_KEY: Mapping[str, str] = MappingProxyType(
    {
        "manufacturer": "manufacturer",
        "part_number": "part_number",
        "material_item": "product_description",
        "equipment_category": "material_category",
        "quantity": "quantity",
        "specification_section": "specification_section",
        "transmittal_number": "submittal_number",
        "action_stamp": "submittal_status",
        "date_submitted": "submittal_date",
        "date_returned": "approval_date",
    }
)


@dataclass(frozen=True)
class ReferenceField:
    """One field the generator composed into a document, before rendering.

    `printed_label` is the label the *plan* chose, which may be an alternate
    where `INCONSISTENT_FIELD_LABEL` was injected; `value` is what the page will
    show. Both come from the pre-render model, so neither has been through a
    renderer, a PDF, or a parser.

    `value` may be empty: `MISSING_OR_BLANK_FIELD` is one of the five
    irregularity classes the synthetic layer carries, and a reference set that
    could not represent a blanked field could not score the documents that have
    one. A blank field is **not** a printed field for recall's purposes, which
    is what `printed_terms` below encodes.
    """

    printed_label: str
    value: str
    vocabulary_term: str | None

    @property
    def is_printed(self) -> bool:
        return bool(self.value.strip())


@dataclass(frozen=True)
class ReferenceDocument:
    """One synthetic document's pre-render model, keyed by its ingest identifier.

    `document_model_hash` is the generator's digest over the reproduced model,
    already required equal to the manifest's by `build_reference_set`. It is
    carried so a figure can name the exact reference it was scored against
    rather than "the reference set".
    """

    document_id: str
    document_model_hash: str
    fields: tuple[ReferenceField, ...]

    def printed_terms(self) -> tuple[str, ...]:
        """Vocabulary terms this document actually printed a value for.

        Sorted and **not** deduplicated away from their count: a transmittal
        printing five items prints `manufacturer` five times, and recall's
        denominator is the number of printed fields rather than the number of
        distinct field names.
        """
        return tuple(
            sorted(
                field.vocabulary_term
                for field in self.fields
                if field.vocabulary_term is not None and field.is_printed
            )
        )

    def printed_values(self, term: str) -> tuple[str, ...]:
        """Every value this document printed for one vocabulary term, in order.

        The expected side of SC-013's character-for-character comparison. Order
        is the generator's field order, which is the printed order.
        """
        return tuple(
            field.value
            for field in self.fields
            if field.vocabulary_term == term and field.is_printed
        )


@dataclass(frozen=True)
class ReferenceSet:
    """Every synthetic document's verified pre-render model.

    Constructed only through `build_reference_set`, which verifies before it
    returns. There is no constructor that yields an unverified set, because
    "verify before any figure is computed" is otherwise a rule someone has to
    remember at each call site.
    """

    documents: Mapping[str, ReferenceDocument]
    verified_digests: int

    def __post_init__(self) -> None:
        if not self.documents:
            raise ReferenceSetError(
                "FR-067 / FR-068: the reference set is empty, so every accuracy figure "
                "would be computed against nothing and would pass. An empty population "
                "fails rather than passes."
            )

    def document(self, document_id: str) -> ReferenceDocument:
        found = self.documents.get(document_id)
        if found is None:
            raise ReferenceSetError(
                f"FR-067: {document_id} has no entry in the reference set, so nothing can "
                f"be scored for it. A value is never scored against the chunk it was read "
                f"out of as a fallback — that is the derived oracle the requirement "
                f"forbids."
            )
        return found

    def printed_counts(self) -> Mapping[str, int]:
        """Printed fields per vocabulary term, over the whole synthetic layer.

        Recall's denominator, per FR-060: "the fields the generator recorded as
        printed", never the values the run stored.
        """
        counts: dict[str, int] = {}
        for document in self.documents.values():
            for term in document.printed_terms():
                counts[term] = counts.get(term, 0) + 1
        return dict(sorted(counts.items()))


def _manifest_hashes(root: Path | None) -> dict[str, str]:
    """`document_id -> document_model_hash`, from the committed synthetic manifests.

    Raises:
        ReferenceSetError: a synthetic entry carries no document-model digest.
            Unreachable through `SyntheticEntry`, which requires one at
            construction — checked anyway, because the alternative to a loud
            failure here is a silent one where a document is simply absent from
            the verification and nobody notices which.
    """
    hashes: dict[str, str] = {}
    for document in iter_entries(root):
        if document.layer != LAYER_SYNTHETIC:
            continue
        entry = document.entry
        if not isinstance(entry, SyntheticEntry):
            raise ReferenceSetError(
                f"{document.entry.location}: a {LAYER_SYNTHETIC} manifest entry is not a "
                f"SyntheticEntry, so it carries no document-model digest to verify against"
            )
        hashes[mint_document_id(entry.location.removesuffix(".pdf"))] = entry.document_model_hash
    return hashes


def build_reference_set(root: Path | None = None) -> ReferenceSet:
    """Reproduce the pre-render document models and verify them (FR-067).

    Args:
        root: the corpus root to read manifests from. `None` uses the committed
            one, which is the ordinary path; a test passes a temporary tree.

    Returns:
        The verified reference set, keyed by the same document identifiers the
        ingestion job mints — so a figure joins to it without a second naming
        convention.

    Raises:
        ReferenceSetError: the reproduction produced a document the manifests do
            not list, a manifest lists a document the reproduction did not
            produce, or a digest disagrees. All three abort **before any figure
            is computed**, which is the ordering FR-067 states.

    **The reproduction writes nothing.** `compose_layer` plans, injects and
    composes the layer in memory; `generate_corpus` is the function that renders
    and writes, and it is deliberately not the one called here. A verification
    that rewrote the corpus it was verifying would be comparing a file against
    itself.
    """
    reproduced = compose_layer()
    if not reproduced:
        raise ReferenceSetError(
            "FR-067: reproducing the synthetic layer from the committed generation "
            "inputs produced zero documents. Every accuracy figure would then be "
            "computed against an empty reference and would pass."
        )

    manifest_hashes = _manifest_hashes(root)
    documents: dict[str, ReferenceDocument] = {}
    mismatches: list[str] = []

    for generated in reproduced:
        document_id = mint_document_id(generated.plan.document_id)
        expected = manifest_hashes.pop(document_id, None)
        if expected is None:
            raise ReferenceSetError(
                f"FR-067: the reproduction produced {document_id}, which no committed "
                f"synthetic manifest lists. The reference set and the corpus being "
                f"measured must range over the same documents, or a figure's denominator "
                f"counts one population and its numerator another."
            )
        if generated.model_hash != expected:
            mismatches.append(
                f"{document_id}: reproduced {generated.model_hash}, manifest records {expected}"
            )
            continue
        documents[document_id] = ReferenceDocument(
            document_id=document_id,
            document_model_hash=generated.model_hash,
            fields=_reference_fields(generated.model.fields),
        )

    if mismatches:
        raise ReferenceSetError(
            f"FR-067: {len(mismatches)} reproduced document model(s) do not match the "
            f"digest their manifest entry records, so the reference set is not the "
            f"committed one and no figure is computed against it: " + "; ".join(sorted(mismatches))
        )
    if manifest_hashes:
        raise ReferenceSetError(
            f"FR-067: the committed manifests list {sorted(manifest_hashes)}, which the "
            f"reproduction did not produce. A document present in the corpus and absent "
            f"from the reference would be scored against nothing."
        )

    return ReferenceSet(documents=documents, verified_digests=len(documents))


def _reference_fields(fields: Iterable[object]) -> tuple[ReferenceField, ...]:
    """Map the generator's `FieldValue` list onto vocabulary-tagged fields.

    The generator's model carries `(label, value)` pairs and not its own field
    keys — the key is spent by the time the model is composed — so the term is
    resolved by matching the printed label against the committed field-label
    vocabulary. That vocabulary is the one shared input FR-050 permits, and it
    is committed data rather than generator code.
    """
    from model.corpus.codes import VOCABULARY, fold_label

    index: dict[str, str] = {}
    for key in VOCABULARY.field_keys:
        labels = VOCABULARY.labels(key)
        index[fold_label(labels.canonical_label)] = key
        for alternate in labels.alternate_labels:
            index[fold_label(alternate)] = key

    resolved: list[ReferenceField] = []
    for field in fields:
        label = str(getattr(field, "label", ""))
        value = str(getattr(field, "value", ""))
        generator_key = index.get(fold_label(label.rstrip(":").strip()))
        resolved.append(
            ReferenceField(
                printed_label=label,
                value=value,
                vocabulary_term=(
                    VOCABULARY_BY_GENERATOR_KEY.get(generator_key)
                    if generator_key is not None
                    else None
                ),
            )
        )
    return tuple(resolved)


def unmeasured_layers(measured: Sequence[str]) -> Mapping[str, str]:
    """Layers published as not measured, with the reason each carries (SC-047).

    Returned as a mapping rather than a flag so the report prints the reason
    beside the layer. A layer row that was blank, or that read `0/0`, would be
    the thing SC-047 counts at zero.
    """
    return MappingProxyType(
        {layer: REAL_LAYER_NOT_MEASURED for layer in ("REAL",) if layer not in set(measured)}
    )
