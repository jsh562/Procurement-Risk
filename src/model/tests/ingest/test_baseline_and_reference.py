"""FR-050 / FR-067: the honest opponent, and the reference it is scored against.

Two modules that must never meet, tested in one file because what matters about
each is its relationship to the other.

**The reference set is the answer key** (`ingest/reference.py`): the generator's
pre-render document model, reproduced from committed inputs and required equal
to each manifest's digest before any figure exists.

**The baseline is an opponent** (`ingest/baseline.py`): a deterministic
extractor authored from the rendered text alone. It may not read the answer key,
and the committed import contract enforces that — `lint-imports` is where the
enforcement lives, and `tests/checks` is where the contract's presence is
asserted. What is asserted here is the consequence: the baseline's own
label-to-term mapping agrees with the reference's, so the two describe the same
fields without sharing a module.

**And the baseline is scored, on the real corpus.** A baseline nobody ran is a
baseline whose figures nobody can trust. These tests run it over all 25
committed transmittals and score it against the verified reference — which is
also the strongest available evidence that the declared *strong* label is not
flattery.
"""

from __future__ import annotations

import glob
from collections import Counter
from pathlib import Path

import pytest

from model.compute.metrics import FieldCounts, per_field_figures
from model.ingest.baseline import (
    BASELINE_ID,
    BASELINE_INDEPENDENCE,
    DOCUMENT_SCOPED_TERMS,
    TERM_BY_LABEL_KEY,
    BaselinePage,
    extract_document,
    label_index,
)
from model.ingest.documents import mint_document_id
from model.ingest.parse import read_pages
from model.ingest.reference import (
    REAL_LAYER_NOT_MEASURED,
    VOCABULARY_BY_GENERATOR_KEY,
    ReferenceSet,
    ReferenceSetError,
    build_reference_set,
    unmeasured_layers,
)
from model.ingest.report import (
    DECLARED_BASELINE_LABEL,
    ReportError,
    declared_baseline_label,
    extraction_quality_section,
    observed_baseline_label,
)

ENTRY_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENTRY_ROOT.parents[1]

SYNTHETIC_LAYER = "SYNTHETIC"


def synthetic_documents() -> list[Path]:
    return [
        Path(path) for path in sorted(glob.glob(str(REPO_ROOT / "data/corpus/synthetic/*/*.pdf")))
    ]


@pytest.fixture(scope="module")
def reference() -> ReferenceSet:
    return build_reference_set()


@pytest.fixture(scope="module")
def baseline_by_document() -> dict[str, tuple]:
    found = {}
    for path in synthetic_documents():
        pages = [BaselinePage(number=page.number, lines=page.lines) for page in read_pages(path)]
        found[mint_document_id(path.stem)] = extract_document(pages)
    return found


# ---------------------------------------------------------------------------
# FR-067 — the reference set
# ---------------------------------------------------------------------------


def test_every_reproduced_document_matches_its_manifest_digest(reference: ReferenceSet) -> None:
    """The verification is total over the synthetic layer and happens before any
    figure. A single mismatch aborts rather than being reported beside the
    figures it would have invalidated."""
    assert reference.verified_digests == len(reference.documents)
    assert len(reference.documents) == len(synthetic_documents())
    assert all(
        document.document_model_hash.startswith("sha256:")
        for document in reference.documents.values()
    )


def test_the_reference_reports_printed_fields_not_distinct_names(reference: ReferenceSet) -> None:
    """Recall's denominator counts printed *fields*: a transmittal listing two
    items prints `manufacturer` twice, and counting the distinct name once would
    denominate recall on a population the document does not have."""
    counts = reference.printed_counts()
    assert counts["manufacturer"] > len(reference.documents)
    assert counts["submittal_number"] == len(reference.documents)


def test_an_unknown_document_is_refused_rather_than_scored_against_its_chunk(
    reference: ReferenceSet,
) -> None:
    """FR-067's prohibition, as a refusal: there is no fallback to the chunk a
    value was read out of, because that is the derived oracle."""
    with pytest.raises(ReferenceSetError, match="FR-067"):
        reference.document("ufgs-23-52-00")


def test_the_real_layer_is_published_as_not_measured_with_its_reason() -> None:
    """SC-047: zero layer rows blank or `0/0`."""
    unmeasured = unmeasured_layers([SYNTHETIC_LAYER])
    assert unmeasured == {"REAL": REAL_LAYER_NOT_MEASURED}
    assert "retrieved, not generated" in REAL_LAYER_NOT_MEASURED


# ---------------------------------------------------------------------------
# FR-050 — the baseline
# ---------------------------------------------------------------------------


def test_the_baseline_and_the_reference_agree_about_which_terms_are_which() -> None:
    """The two mappings are deliberately separate modules — the baseline may not
    import the reference, which reaches the generator — so the agreement is
    asserted here rather than shared. A divergence would show up as a baseline
    that silently stopped finding a field, which reads as a weak opponent."""
    assert dict(TERM_BY_LABEL_KEY) == dict(VOCABULARY_BY_GENERATOR_KEY)


def test_the_baseline_recognises_canonical_and_alternate_labels() -> None:
    """`INCONSISTENT_FIELD_LABEL` is one of the five irregularity classes the
    synthetic layer carries. A baseline recognising only canonical labels would
    score zero on every document carrying it, and would be losing for a reason
    that has nothing to do with extraction."""
    index = label_index()
    assert index["manufacturer"] == "manufacturer"
    assert index["mfr"] == "manufacturer"
    assert index["make"] == "manufacturer"


def test_the_baseline_reads_the_inline_layout() -> None:
    values = extract_document(
        [
            BaselinePage(
                number=1,
                lines=(
                    "PRJ-001-T0001-R0 | Page 1 of 2",
                    "Transmittal No.: PRJ-001-T0001",
                    "Manufacturer: Norhelm Transformer Wks.",
                ),
            )
        ]
    )
    assert [(value.field_name, value.value_text) for value in values] == [
        ("submittal_number", "PRJ-001-T0001"),
        ("manufacturer", "Norhelm Transformer Wks."),
    ]


def test_the_baseline_reads_the_stacked_layout() -> None:
    values = extract_document(
        [
            BaselinePage(
                number=1,
                lines=(
                    "PRJ-002-T0002-R0 | Page 1 of 2",
                    "Manufacturer:",
                    "Halvard Climate",
                ),
            )
        ]
    )
    assert [(value.field_name, value.value_text) for value in values] == [
        ("manufacturer", "Halvard Climate")
    ]


def test_a_blank_stacked_field_is_not_read_as_the_next_label() -> None:
    """`Contract No.:` followed by `Vendor:` is a blanked field, not a contract
    number of `Vendor:`. This is `MISSING_OR_BLANK_FIELD` and it must produce
    no value."""
    values = extract_document(
        [
            BaselinePage(
                number=1,
                lines=("X | Page 1 of 1", "Contract No.:", "Manufacturer:", "Halvard Climate"),
            )
        ]
    )
    assert [(value.field_name, value.value_text) for value in values] == [
        ("manufacturer", "Halvard Climate")
    ]


def test_the_baseline_anchors_a_page_split_value_on_the_page_printing_it() -> None:
    """The same anchor rule FR-029 fixes for the model path. Scoring two
    extractors whose citations followed different rules would compare more than
    the extraction."""
    values = extract_document(
        [
            BaselinePage(number=1, lines=("X | Page 1 of 2", "Manufacturer:")),
            BaselinePage(number=2, lines=("X | Page 2 of 2", "Halvard Climate")),
        ]
    )
    (value,) = values
    assert value.value_text == "Halvard Climate"
    assert value.page_number == 2


def test_the_baseline_numbers_items_from_one_and_groups_document_fields_at_zero() -> None:
    values = extract_document(
        [
            BaselinePage(
                number=1,
                lines=(
                    "X | Page 1 of 2",
                    "Transmittal No.: PRJ-001-T0001",
                    "Material Item: Switchboard (Tag 1)",
                    "Manufacturer: Alpha",
                    "Material Item: Cooling Tower (Tag 2)",
                    "Manufacturer: Beta",
                ),
            )
        ]
    )
    ordinals = {(value.field_name, value.value_text): value.item_ordinal for value in values}
    assert ordinals[("submittal_number", "PRJ-001-T0001")] == 0
    assert ordinals[("manufacturer", "Alpha")] == 1
    assert ordinals[("manufacturer", "Beta")] == 2


def test_the_baseline_refuses_an_empty_document() -> None:
    with pytest.raises(ValueError, match="FR-068"):
        extract_document([])


def test_the_document_scoped_terms_are_a_subset_of_the_mapped_terms() -> None:
    assert set(TERM_BY_LABEL_KEY.values()) >= DOCUMENT_SCOPED_TERMS


# ---------------------------------------------------------------------------
# The baseline, scored over the whole synthetic layer
# ---------------------------------------------------------------------------


def score_baseline(
    reference: ReferenceSet, baseline_by_document: dict[str, tuple]
) -> tuple[FieldCounts, ...]:
    """Per-field precision and recall counts for the baseline, over every document.

    Multiset matching: a document printing `manufacturer` twice can have one
    right and one wrong, and set comparison would score that as a hit.
    """
    stored: Counter[str] = Counter()
    matched: Counter[str] = Counter()
    printed: Counter[str] = Counter()
    recovered: Counter[str] = Counter()

    for document_id, values in baseline_by_document.items():
        document = reference.document(document_id)
        for term in document.printed_terms():
            printed[term] += 1
        by_term: dict[str, list[str]] = {}
        for value in values:
            by_term.setdefault(value.field_name, []).append(value.value_text)
        for term, produced in by_term.items():
            expected = list(document.printed_values(term))
            stored[term] += len(produced)
            for text in produced:
                if text in expected:
                    expected.remove(text)
                    matched[term] += 1
                    recovered[term] += 1

    return tuple(
        FieldCounts(
            field=term,
            layer=SYNTHETIC_LAYER,
            stored=stored[term],
            stored_matching=matched[term],
            printed=printed[term],
            printed_recovered=recovered[term],
        )
        for term in sorted(set(stored) | set(printed))
        if stored[term] and printed[term]
    )


def test_the_baseline_is_a_real_opponent_over_the_committed_corpus(
    reference: ReferenceSet, baseline_by_document: dict[str, tuple]
) -> None:
    """Principle VIII: the only baseline whose defeat carries information is one
    that could have won. This scores it, so "strong" is evidence rather than a
    label."""
    counts = score_baseline(reference, baseline_by_document)
    assert counts, "the baseline produced no scoreable field over 25 transmittals"
    figures = per_field_figures(counts)
    for figure in figures:
        assert figure.precision.point > 0.5, (
            f"the baseline's precision on {figure.field} is {figure.precision.point:.3f}; "
            f"an opponent this weak makes every comparison against it flattery"
        )
        assert figure.recall.point > 0.5


def test_a_defeated_baseline_reads_as_weak_and_a_tie_reads_as_strong(
    reference: ReferenceSet, baseline_by_document: dict[str, tuple]
) -> None:
    """The observed label's criterion, exercised in both directions.

    One tie is enough for `strong`, because the claim being tested is "a
    deterministic opponent could not do this" and a tie refutes it.
    """
    baseline_figures = per_field_figures(score_baseline(reference, baseline_by_document))
    dominated = per_field_figures(
        FieldCounts(
            field=figure.field,
            layer=figure.layer,
            stored=figure.precision.denominator,
            stored_matching=max(figure.precision.numerator - 1, 0),
            printed=figure.recall.denominator,
            printed_recovered=max(figure.recall.numerator - 1, 0),
        )
        for figure in baseline_figures
    )
    # Arguments are (model, baseline). A baseline that beats the model on every
    # cell is strong; one the model dominates on every cell is weak.
    assert observed_baseline_label(dominated, baseline_figures) == "strong"
    assert observed_baseline_label(baseline_figures, dominated) == "weak"


def test_the_observed_label_refuses_an_empty_comparison(
    reference: ReferenceSet, baseline_by_document: dict[str, tuple]
) -> None:
    """It would return `weak` by default, which is the flattering answer."""
    figures = per_field_figures(score_baseline(reference, baseline_by_document))
    other_layer = per_field_figures(
        FieldCounts(
            field=figure.field,
            layer="pooled",
            stored=figure.precision.denominator,
            stored_matching=figure.precision.numerator,
            printed=figure.recall.denominator,
            printed_recovered=figure.recall.numerator,
        )
        for figure in figures
    )
    with pytest.raises(ReportError, match="FR-050"):
        observed_baseline_label(figures, other_layer)


# ---------------------------------------------------------------------------
# FR-050 / FR-060 — item 12
# ---------------------------------------------------------------------------


def test_the_declared_label_is_a_committed_constant_not_a_run_time_value() -> None:
    """FR-050 fixes it **before any figure exists**, and a value a run could
    compute is one that could be computed after the figures."""
    assert DECLARED_BASELINE_LABEL == "strong"
    assert declared_baseline_label(independent=True, template_driven=True) == "strong"
    assert declared_baseline_label(independent=False, template_driven=True) == "weak"
    assert declared_baseline_label(independent=True, template_driven=False) == "weak"


def test_the_quality_section_publishes_both_labels_the_denominators_and_no_f1(
    reference: ReferenceSet, baseline_by_document: dict[str, tuple]
) -> None:
    baseline_figures = per_field_figures(score_baseline(reference, baseline_by_document))
    model_figures = per_field_figures(
        FieldCounts(
            field=figure.field,
            layer=figure.layer,
            stored=figure.precision.denominator,
            stored_matching=max(figure.precision.numerator - 1, 0),
            printed=figure.recall.denominator,
            printed_recovered=max(figure.recall.numerator - 1, 0),
        )
        for figure in baseline_figures
    )
    section = extraction_quality_section(
        run_id="run-1",
        model_figures=model_figures,
        baseline_figures=baseline_figures,
        unmeasured_layers=unmeasured_layers([SYNTHETIC_LAYER]),
    )
    assert section.item == 12
    assert "Declared label: strong" in section.body
    assert "Observed label: strong" in section.body
    assert "F1 is not published" in section.body
    assert "continuity-corrected Wilson 95%" in section.body
    assert "Layer `REAL` is not measured" in section.body
    assert BASELINE_ID in section.body
    assert BASELINE_INDEPENDENCE in section.body
    # Every figure prints its denominator (FR-060).
    assert all("/" in str(figure.value) for figure in section.figures)


def test_a_label_disagreement_is_published_as_a_finding(
    reference: ReferenceSet, baseline_by_document: dict[str, tuple]
) -> None:
    """Principle VIII: published as it stands, not reconciled by revising either
    label. Constructed by giving the model a strictly better figure in every
    cell, which is what `weak` observed means."""
    baseline_figures = per_field_figures(score_baseline(reference, baseline_by_document))
    weakened = per_field_figures(
        FieldCounts(
            field=figure.field,
            layer=figure.layer,
            stored=figure.precision.denominator,
            stored_matching=max(figure.precision.numerator - 1, 0),
            printed=figure.recall.denominator,
            printed_recovered=max(figure.recall.numerator - 1, 0),
        )
        for figure in baseline_figures
    )
    section = extraction_quality_section(
        run_id="run-1",
        model_figures=baseline_figures,
        baseline_figures=weakened,
        unmeasured_layers=unmeasured_layers([SYNTHETIC_LAYER]),
    )
    assert "Finding — the two labels disagree" in section.body
    assert "Declared label: strong" in section.body
    assert "Observed label: weak" in section.body


def test_the_quality_section_refuses_a_table_with_no_figures() -> None:
    with pytest.raises(ReportError, match="FR-050"):
        extraction_quality_section(
            run_id="run-1",
            model_figures=(),
            baseline_figures=(),
            unmeasured_layers={},
        )
