"""Scoring the two extractors against the reference set (FR-050, FR-060, FR-067).

Report item 12 publishes per-field precision and recall for the model path
beside the deterministic baseline's, over the same documents. Everything it
needs existed and nothing joined them: `reference.build_reference_set` produced
the expected side, `baseline.extract_document` produced the opponent's answers,
`compute.metrics.per_field_figures` turned counts into figures with intervals —
and the only code that ever put the three together lived in
`src/model/tests/ingest/test_baseline_and_reference.py`. A comparison that
exists only inside a test is a comparison the report cannot publish.

**The matching is multiset, not set.** A document printing `manufacturer` twice
can have one right and one wrong; comparing sets would score that as a hit, and
a precision figure that cannot see a wrong value among right ones measures
nothing. Each produced value consumes one expected value of the same term, and a
term with more produced than expected leaves the surplus unmatched — which is
what precision is for.

**The comparison is character-for-character on the printed text** (SC-013). The
expected side is the pre-render document model's own string; the produced side
is what the extractor read off the page. No normalization is applied here on
either side: a normalizer would be a third judgement about what "the same value"
means, and it would be applied to the answer key as readily as to the answer.

**Only the synthetic layer is scored, and the real layer's absence is a
published reason rather than a missing row** (FR-060, SC-047).
`reference.unmeasured_layers` carries that reason, and item 12 prints it.

This module holds no connection and reaches no provider. It reads the rendered
pages for the baseline through `model.ingest.parse`, which is the ingestion
package's single page reader (FR-008) — the baseline itself may not import it,
because `parse` reaches `corpus.derive` and thence the generator, so the read
happens here and the text is handed over as strings. That is also the honest
shape: the baseline is given the rendered text and nothing else.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from model.compute.metrics import FieldCounts
from model.corpus.manifest import LAYER_SYNTHETIC
from model.ingest.baseline import BaselinePage, BaselineValue, extract_document
from model.ingest.documents import DocumentRecord
from model.ingest.reference import ReferenceSet

__all__ = [
    "MEASURED_LAYER",
    "baseline_values",
    "measured_documents",
    "score_against_reference",
]

#: The one layer any accuracy figure is computed on (SC-012, FR-060). Named
#: rather than written at each call site so the layer a figure is labelled with
#: and the layer it was scored on are the same string.
MEASURED_LAYER: str = "SYNTHETIC"


def score_against_reference(
    reference: ReferenceSet,
    produced: Mapping[str, Iterable[tuple[str, str]]],
    *,
    layer: str = MEASURED_LAYER,
) -> tuple[FieldCounts, ...]:
    """Per-field counts for one extractor over the documents it read.

    Args:
        reference: the verified pre-render document models (FR-067). Verified
            *before* this is called — `build_reference_set` verifies before it
            returns, and there is no constructor that yields an unverified set.
        produced: `document_id -> (field_name, value_text)` pairs, in printed
            order. Deliberately a pair rather than either extractor's own value
            type: the model path produces `writer.PreparedValue` and the
            baseline produces `baseline.BaselineValue`, and a scorer taking one
            of them would have to be written twice or would have to import the
            other side into this comparison.
        layer: the layer these documents are on. Defaulted to the only one that
            has a reference at all.

    Returns:
        One `FieldCounts` per field with a non-empty denominator on **both**
        sides, sorted by field. A cell with an empty denominator on either side
        is dropped rather than published as `0/0`, which SC-047 admits nowhere
        and `Proportion` refuses at construction.

    Raises:
        ReferenceSetError: a document has no entry in the reference set. Not
            defaulted to "score it against nothing": FR-067's whole content is
            that the expected side comes from the pre-render model, and a
            document absent from it is a document nothing can be scored for.

    **Recall's denominator is the reference's, never the extractor's.** It is
    counted from `printed_terms()` over the documents scored, so a field the
    extractor never produced still appears in the denominator — which is the
    only thing recall is for.
    """
    stored: Counter[str] = Counter()
    matched: Counter[str] = Counter()
    printed: Counter[str] = Counter()
    recovered: Counter[str] = Counter()

    for document_id, values in produced.items():
        document = reference.document(document_id)
        for term in document.printed_terms():
            printed[term] += 1
        by_term: dict[str, list[str]] = {}
        for field_name, value_text in values:
            by_term.setdefault(field_name, []).append(value_text)
        for term, produced_values in by_term.items():
            expected = list(document.printed_values(term))
            stored[term] += len(produced_values)
            for text in produced_values:
                if text in expected:
                    # Consumed, so a second identical produced value cannot
                    # match the same printed field twice.
                    expected.remove(text)
                    matched[term] += 1
                    recovered[term] += 1

    return tuple(
        FieldCounts(
            field=term,
            layer=layer,
            stored=stored[term],
            stored_matching=matched[term],
            printed=printed[term],
            printed_recovered=recovered[term],
        )
        for term in sorted(set(stored) | set(printed))
        if stored[term] and printed[term]
    )


def baseline_values(records: Sequence[DocumentRecord]) -> dict[str, tuple[BaselineValue, ...]]:
    """Run the deterministic baseline over the rendered documents (FR-050).

    Args:
        records: the documents to read, which the caller has already restricted
            to the layer being measured.

    Returns:
        `document_id -> values found`, in printed order.

    The pages are read here and handed to `extract_document` as plain strings.
    The baseline may not import `model.ingest.parse` — that module reaches
    `corpus.derive` and thence the per-vendor templates, which is the answer key
    the import-linter contract keeps it away from — so the read is the caller's
    and the opponent is given the rendered text alone.
    """
    # Deferred, for the reason `ingest/cli.py` defers its own: `parse` pulls in
    # pdfplumber, and a module-level import would make every consumer of this
    # module's scoring function pay for a PDF reader it does not use.
    from model.ingest.parse import read_pages

    found: dict[str, tuple[BaselineValue, ...]] = {}
    for record in records:
        pages = [
            BaselinePage(number=page.number, lines=page.lines) for page in read_pages(record.path)
        ]
        found[record.document_id] = extract_document(pages)
    return found


def measured_documents(records: Sequence[DocumentRecord]) -> tuple[DocumentRecord, ...]:
    """The records on the one layer that has a reference set (FR-067)."""
    return tuple(record for record in records if record.source_kind == LAYER_SYNTHETIC)
