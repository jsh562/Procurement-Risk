"""T094-T096: the last three items of FR-071's closed content list.

Items 8, 14 and 21 were the entries the list required and nothing built, which is
why `build_report` refused every complete report until now. Each is asserted here
for the property that makes it worth publishing at all rather than for its
wording:

* **item 8 (SC-005)** — the chunk counts are published *against* a declared
  estimate, and a count outside it is a **published result with its cause**, not
  an exception and not an estimate quietly restated to match;
* **item 14 (FR-058)** — the count of printed-but-unattempted fields, which is
  the escape hatch that makes declaring the attempted subset in advance safe. A
  term the generator printed, nobody attempted, and nothing recorded a reason for
  is published as a defect in the declaration rather than absorbed;
* **item 21 (FR-019)** — the parity bounds are **read from the committed probe
  set** and the section refuses to render if the measurement was taken against a
  different pair. A report carrying its own copy of a bound can be edited to
  match an observation, which is exactly what "declared before the comparison"
  exists to prevent.

The file closes with the assertion the three tasks exist for: a **complete
twenty-one-item section set, built entirely from real builders with no
placeholders**, rendering through `build_report` without it refusing on a
missing item.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from model.compute.confidence import SIGNAL_DOMAIN, compute_confidence
from model.compute.metrics import FieldCounts, per_field_figures
from model.ingest.chunker import Chunk, DocumentChunking
from model.ingest.cli import exclusion_section, select_extraction_documents
from model.ingest.documents import DocumentRecord
from model.ingest.embed import ParityMeasurement
from model.ingest.failures import outcome_counts
from model.ingest.report import (
    CHUNK_ESTIMATE_MAXIMUM,
    CHUNK_ESTIMATE_MINIMUM,
    DECLARED_BOUND_KEYS,
    PROBE_SET_ARTIFACT,
    RETIRED_AT_RUN_TIME_REASON,
    AttemptLedger,
    CarriedClaim,
    ChunkVector,
    InvocationLedger,
    ReportError,
    SampledClaim,
    attempt_ledger_section,
    build_report,
    chunk_count_section,
    chunk_identity_section,
    chunking_section,
    collect_total_checks,
    confidence_section,
    declared_parity_bounds,
    disposition_section,
    encoder_parity_section,
    extraction_quality_section,
    failure_breakdown_section,
    human_inspection_section,
    index_procedure_section,
    measure_near_duplicates,
    near_duplicate_section,
    page_split_section,
    prj_000_section,
    profile_chunkings,
    recognition_error_section,
    reconciliation_section,
    reproduction_section,
    scope_labels_section,
    tally_confidence,
    total_checks_section,
    unattempted_fields_section,
)
from model.ingest.runs import DECLARED_POLICY
from model.ingest.writer import MultiChunkCounts
from model.llm.schemas import EXCLUDED_TERMS, TRANSMITTAL_FIELD_SUBSET

RUN_ID = "00000000-0000-4000-8000-00000000e006"
TRACE_ID = "0123456789abcdef0123456789abcdef"

ATTEMPTED_FIELDS = tuple(entry.name for entry in TRANSMITTAL_FIELD_SUBSET)


def _chunk(document_id: str, ordinal: int) -> Chunk:
    return Chunk(
        document_id=document_id,
        document_type="specification",
        project_id="PRJ-000",
        page_number=1 + ordinal // 4,
        ordinal=ordinal,
        body_text="Submit shop drawings for the equipment specified in this section.",
        boundary_class="structural",
        structural_identifier="1.1",
        content_pieces=12,
    )


def profile_with(*, real: int, synthetic: int):
    """A chunking profile carrying a chosen number of chunks on each layer.

    The counts are what item 8 is about, so they are the parameter; everything
    else about the chunks is uniform and deliberately uninteresting.
    """
    entries = []
    if real:
        entries.append(
            (
                "REAL",
                DocumentChunking(
                    document_id="ufgs-23-52-00",
                    chunker_version="e006-chunker/test",
                    chunks=tuple(_chunk("ufgs-23-52-00", index) for index in range(real)),
                ),
            )
        )
    if synthetic:
        entries.append(
            (
                "SYNTHETIC",
                DocumentChunking(
                    document_id="prj-001-t0001-r0",
                    chunker_version="e006-chunker/test",
                    chunks=tuple(_chunk("prj-001-t0001-r0", index) for index in range(synthetic)),
                ),
            )
        )
    return profile_chunkings(entries)


# ---------------------------------------------------------------------------
# Item 8 — SC-005, the chunk counts against the declared estimate (T094)
# ---------------------------------------------------------------------------


def test_the_declared_estimate_is_the_architectures_five_to_fifteen_thousand() -> None:
    assert (CHUNK_ESTIMATE_MINIMUM, CHUNK_ESTIMATE_MAXIMUM) == (5_000, 15_000)


def test_a_total_inside_the_estimate_publishes_both_bounds_and_the_observation() -> None:
    """The estimate is printed whether or not it was met.

    A prediction reported only when it fails is one nobody can see was ever
    made, so both bounds are published as figures of their own rather than
    mentioned in prose when they are breached.
    """
    section = chunk_count_section(run_id=RUN_ID, profile=profile_with(real=4_000, synthetic=2_466))
    assert section.item == 8
    labels = {figure.label: figure.value for figure in section.figures}
    assert labels["Chunks cut — total"] == 6_466
    assert labels["Chunks cut — REAL"] == 4_000
    assert labels["Chunks cut — SYNTHETIC"] == 2_466
    assert labels["Declared estimate — lower bound"] == CHUNK_ESTIMATE_MINIMUM
    assert labels["Declared estimate — upper bound"] == CHUNK_ESTIMATE_MAXIMUM
    assert "inside the estimate" in section.body
    assert section.total_checks[0].outcome == "held"
    assert section.total_checks[0].count == 6_466


def test_a_total_below_the_estimate_is_a_published_result_with_its_cause() -> None:
    """SC-005 explicitly forbids the other repair: restating the estimate.

    So the builder returns a section rather than raising, and the deviation
    lands in the total check's outcome where a reader sees it beside every other
    check rather than only in prose.
    """
    section = chunk_count_section(run_id=RUN_ID, profile=profile_with(real=300, synthetic=200))
    assert "below the estimate" in section.body
    assert "not restated to match the result" in section.body
    assert "-4,500 chunks from the nearer bound" in section.body
    assert section.total_checks[0].outcome.startswith("DEVIATION (below)")
    # The cause is one of the candidates declared in advance, not one invented
    # after the count was seen.
    assert "254-piece content budget" in section.body


def test_a_total_above_the_estimate_names_the_causes_that_push_it_up() -> None:
    section = chunk_count_section(run_id=RUN_ID, profile=profile_with(real=15_000, synthetic=1))
    assert "above the estimate" in section.body
    assert "+1 chunks from the nearer bound" in section.body
    assert "sentence-level splitting" in section.body
    assert "254-piece content budget" not in section.body
    assert section.total_checks[0].outcome.startswith("DEVIATION (above)")


def test_the_bounds_are_inclusive_at_both_ends() -> None:
    """The boundary either side, so `<=` is not written as `<`."""
    for total in (CHUNK_ESTIMATE_MINIMUM, CHUNK_ESTIMATE_MAXIMUM):
        section = chunk_count_section(
            run_id=RUN_ID, profile=profile_with(real=total - 1, synthetic=1)
        )
        assert section.total_checks[0].outcome == "held", total


def test_a_corpus_that_produced_no_chunk_is_refused_rather_than_reported_as_a_deviation() -> None:
    """FR-068: an empty population fails rather than publishing a comparison."""
    empty = profile_chunkings(
        [
            (
                "REAL",
                DocumentChunking(document_id="ufgs-23-52-00", chunker_version="v", chunks=()),
            )
        ]
    )
    with pytest.raises(ReportError, match="SC-005"):
        chunk_count_section(run_id=RUN_ID, profile=empty)


def test_item_eight_counts_the_same_population_item_nine_ranges_over() -> None:
    """One profile object, so the total and the distribution cannot disagree."""
    profile = profile_with(real=4_000, synthetic=2_466)
    counts = chunk_count_section(run_id=RUN_ID, profile=profile)
    distribution = chunking_section(run_id=RUN_ID, profile=profile)
    total = next(figure.value for figure in counts.figures if figure.label == "Chunks cut — total")
    assert total == distribution.total_checks[0].count


# ---------------------------------------------------------------------------
# Item 14 — FR-058, the fields printed but not attempted (T095)
# ---------------------------------------------------------------------------

PRINTED_COUNTS = {
    "manufacturer": 120,
    "part_number": 118,
    "quantity": 120,
    "product_description": 120,
    "material_category": 119,
    "specification_section": 25,
    "submittal_number": 25,
    "submittal_status": 25,
    "submittal_date": 25,
    "approval_date": 21,
}


def test_a_corpus_whose_printed_fields_were_all_attempted_publishes_an_enumerated_zero() -> None:
    """FR-058's construction claim, published rather than left as an absent table."""
    section = unattempted_fields_section(
        run_id=RUN_ID, printed_counts=PRINTED_COUNTS, attempted_fields=ATTEMPTED_FIELDS
    )
    assert section.item == 14
    labels = {figure.label: figure.value for figure in section.figures}
    assert labels["Printed fields not attempted"] == 0
    assert labels["Vocabulary terms printed and not attempted"] == 0
    assert labels["Printed fields the generator recorded"] == sum(PRINTED_COUNTS.values())
    assert "Every term the generator recorded as printed was attempted" in section.body
    assert section.total_checks[0].outcome == "held"


def test_a_printed_term_outside_the_subset_is_counted_with_its_recorded_reason() -> None:
    """The count is of **printed fields**, not of distinct terms.

    A transmittal listing five items prints `unit_price` five times, and a count
    of terms would report that as one — which understates the population the
    declaration missed by exactly the factor that matters.
    """
    printed = dict(PRINTED_COUNTS) | {"unit_price": 117}
    section = unattempted_fields_section(
        run_id=RUN_ID, printed_counts=printed, attempted_fields=ATTEMPTED_FIELDS
    )
    labels = {figure.label: figure.value for figure in section.figures}
    assert labels["Printed fields not attempted"] == 117
    assert labels["Vocabulary terms printed and not attempted"] == 1
    assert "`unit_price`" in section.body
    assert EXCLUDED_TERMS["unit_price"] in section.body
    # Explained, so the declaration is not reported as defective.
    assert section.total_checks[0].outcome == "held"
    assert "Defect in the declaration" not in section.body


def test_a_printed_term_with_no_recorded_reason_is_published_as_a_declaration_defect() -> None:
    """FR-058's "no printed field goes unattempted by construction", tested.

    A term in neither the attempted subset nor the recorded exclusions is
    unattempted and unreported at once, which is the case this item exists to
    surface — so it gets its own heading and fails the total check rather than
    joining the explained rows.
    """
    printed = dict(PRINTED_COUNTS) | {"delivery_ticket_number": 4}
    section = unattempted_fields_section(
        run_id=RUN_ID, printed_counts=printed, attempted_fields=ATTEMPTED_FIELDS
    )
    assert "Defect in the declaration" in section.body
    assert "`delivery_ticket_number`" in section.body
    assert section.total_checks[0].outcome.startswith("FAILED")


def test_a_run_narrowed_by_a_retirement_reports_the_terms_it_did_not_attempt() -> None:
    """The run's own subset, not the committed declaration.

    Publishing the declaration here would report a field as attempted that this
    run never asked for — and the whole point of the item is what this run
    missed. A retirement is a **recorded reason of its own**: a term in the
    committed subset that this run did not attempt was retired in
    `field_vocabulary`, which is FR-024's mechanism working rather than a gap in
    the declaration, and reporting it as a defect would make every narrowed run
    publish a defect it does not have.
    """
    narrowed = tuple(name for name in ATTEMPTED_FIELDS if name != "approval_date")
    section = unattempted_fields_section(
        run_id=RUN_ID, printed_counts=PRINTED_COUNTS, attempted_fields=narrowed
    )
    labels = {figure.label: figure.value for figure in section.figures}
    assert labels["Printed fields not attempted"] == PRINTED_COUNTS["approval_date"]
    assert RETIRED_AT_RUN_TIME_REASON in section.body
    assert "Defect in the declaration" not in section.body
    assert section.total_checks[0].outcome == "held"


def test_an_empty_attempted_subset_or_an_empty_printed_population_is_refused() -> None:
    with pytest.raises(ReportError, match="FR-058"):
        unattempted_fields_section(
            run_id=RUN_ID, printed_counts=PRINTED_COUNTS, attempted_fields=()
        )
    with pytest.raises(ReportError, match="FR-058"):
        unattempted_fields_section(
            run_id=RUN_ID, printed_counts={}, attempted_fields=ATTEMPTED_FIELDS
        )


# ---------------------------------------------------------------------------
# Item 21 — FR-019, the parity bounds and the observed maxima (T096)
# ---------------------------------------------------------------------------


def measurement_from_artifact(
    *, cosine: float = 0.9999997, difference: float = 2.0e-6
) -> ParityMeasurement:
    """A measurement carrying **the committed bounds**, with chosen observations.

    The declared side is read from `data/encoder/probes.json` rather than typed
    here, for the same reason the section reads it: a fixture with its own copy
    of a bound would pass while the artifact said something else.
    """
    declared = declared_parity_bounds()
    return ParityMeasurement(
        declared_cosine_minimum=declared["cosine_similarity_min"],
        declared_max_absolute_difference=declared["max_absolute_per_dimension_difference"],
        observed_minimum_cosine=cosine,
        observed_maximum_absolute_difference=difference,
        per_probe=(
            ("probe-real-1", "REAL", cosine, difference),
            ("probe-synthetic-1", "SYNTHETIC", 0.9999999, 1.0e-6),
        ),
        reference={"library": "sentence-transformers", "sentence_transformers": "3.0.1"},
    )


def test_the_declared_bounds_come_from_the_committed_probe_set() -> None:
    """Read, never restated — the first of ADR-0018's three parts."""
    bounds = declared_parity_bounds()
    assert set(bounds) == set(DECLARED_BOUND_KEYS)
    assert bounds["cosine_similarity_min"] == 0.999999
    assert bounds["max_absolute_per_dimension_difference"] == 1e-05


def test_the_section_publishes_both_bounds_and_both_observed_maxima() -> None:
    section = encoder_parity_section(run_id=RUN_ID, measurement=measurement_from_artifact())
    assert section.item == 21
    labels = {figure.label: figure.value for figure in section.figures}
    assert labels["Declared bound — minimum cosine similarity"] == 0.999999
    assert labels["Declared bound — maximum absolute per-dimension difference"] == 1e-05
    assert labels["Observed minimum cosine similarity"] == 0.9999997
    assert labels["Observed maximum absolute per-dimension difference"] == 2.0e-6
    assert labels["Probes compared"] == 2
    assert "Both bounds hold" in section.body
    assert PROBE_SET_ARTIFACT in section.body
    assert section.total_checks[0].outcome == "held"


def test_the_observed_figures_carry_the_encoder_parity_tolerance() -> None:
    """FR-074, item 19: the tolerance is a field on the figure, not a footnote."""
    section = encoder_parity_section(run_id=RUN_ID, measurement=measurement_from_artifact())
    observed = [figure for figure in section.figures if figure.label.startswith("Observed")]
    assert observed
    for figure in observed:
        assert "encoder parity" in figure.tolerance


def test_a_measurement_taken_against_a_different_bound_is_refused() -> None:
    """The assertion's bound and the published bound must be one number.

    This is the whole content of "declared before the comparison": a section
    that published 1e-4 while the assertion enforced 1e-5 would satisfy the
    requirement's wording in two places and its meaning in neither.
    """
    widened = replace(measurement_from_artifact(), declared_max_absolute_difference=1e-4)
    with pytest.raises(ReportError, match="FR-019"):
        encoder_parity_section(run_id=RUN_ID, measurement=widened)


def test_a_probe_set_covering_one_layer_is_refused() -> None:
    """ADR-0018 accepts the export only against a set spanning both layers."""
    narrowed = replace(
        measurement_from_artifact(),
        per_probe=(("probe-real-1", "REAL", 0.9999999, 1.0e-6),),
    )
    with pytest.raises(ReportError, match="SYNTHETIC"):
        encoder_parity_section(run_id=RUN_ID, measurement=narrowed)


def test_an_empty_probe_set_is_refused() -> None:
    empty = replace(measurement_from_artifact(), per_probe=())
    with pytest.raises(ReportError, match="FR-019"):
        encoder_parity_section(run_id=RUN_ID, measurement=empty)


def test_a_breach_is_published_as_a_failure_of_the_gate_and_the_bound_is_not_widened() -> None:
    """Principle VII. The section renders; it is the run that refuses to embed.

    A builder that raised on a breach would hide the finding the requirement
    exists to surface, and the tempting repair — moving the bound — is the one
    thing FR-019 forbids.
    """
    breached = measurement_from_artifact(cosine=0.99, difference=1e-3)
    section = encoder_parity_section(run_id=RUN_ID, measurement=breached)
    assert "declared bound is breached" in section.body
    assert "not widened to admit the observation" in section.body
    assert section.total_checks[0].outcome.startswith("FAILED")
    # The declared bounds are still the artifact's, unchanged by the breach.
    labels = {figure.label: figure.value for figure in section.figures}
    assert labels["Declared bound — minimum cosine similarity"] == 0.999999


# ---------------------------------------------------------------------------
# FR-071 — the complete list, from real builders, with no placeholder
# ---------------------------------------------------------------------------


def _document(document_id: str, *, transmittal: bool) -> DocumentRecord:
    if transmittal:
        return DocumentRecord(
            document_id=document_id,
            document_type="transmittal",
            project_id="PRJ-001",
            title=document_id,
            source_kind="SYNTHETIC",
            license_basis="{}",
            content_hash="sha256:" + "0" * 64,
            path=Path(f"{document_id}.pdf"),
            generator_id="e002-generator/1",
            generation_seed="20260101",
            generated_at=date(2026, 1, 1),
            fixture_hashes=("sha256:" + "1" * 64,),
            roster_hash="sha256:" + "2" * 64,
        )
    return DocumentRecord(
        document_id=document_id,
        document_type="specification",
        project_id="PRJ-000",
        title=document_id,
        source_kind="REAL",
        license_basis="{}",
        content_hash="sha256:" + "3" * 64,
        path=Path(f"{document_id}.pdf"),
        source_ref="https://example.invalid/ufgs",
        issuing_body="UFGS",
        retrieval_date=date(2026, 1, 1),
    )


def _vector(seed: float) -> np.ndarray:
    base = np.full(8, seed, dtype=np.float32)
    base[0] = 1.0
    return base / np.linalg.norm(base)


def _chunk_vector(document_id: str, *, layer: str, text: str, seed: float) -> ChunkVector:
    return ChunkVector(
        document_id=document_id,
        layer=layer,
        ordinal=0,
        page_number=1,
        heading="REFERENCES",
        body_text=text,
        embedding=_vector(seed),
    )


def _field_figures(stored_matching: int):
    return per_field_figures(
        FieldCounts(
            field=field,
            layer="SYNTHETIC",
            stored=120,
            stored_matching=stored_matching,
            printed=120,
            printed_recovered=stored_matching,
        )
        for field in ("manufacturer", "part_number")
    )


def every_section(profile, measurement: ParityMeasurement):
    """All twenty-one items, each from the builder that owns it.

    No placeholders. `test_total_checks.py` keeps a stubbed set for exercising
    the builder over a complete list while items were still unbuilt; this is the
    other assertion — that the list is now covered by real work, so a report can
    actually be emitted.
    """
    scope = select_extraction_documents(
        [
            _document("prj-001-t0001-r0", transmittal=True),
            _document("ufgs-23-52-00", transmittal=False),
        ]
    )
    vectors = (
        _chunk_vector("ufgs-23-52-00", layer="REAL", text="ASTM A123", seed=0.1),
        _chunk_vector("ufgs-26-05-13", layer="REAL", text="ASTM A123", seed=0.1),
    )
    stored = tuple(
        signals
        for signals in SIGNAL_DOMAIN
        if DECLARED_POLICY.admits(compute_confidence(signals, DECLARED_POLICY.weights))
    )
    rejected = tuple(signals for signals in SIGNAL_DOMAIN if signals not in stored)

    sections = [
        prj_000_section(run_id=RUN_ID, real_documents=26),
        recognition_error_section(run_id=RUN_ID, documents_by_layer={"REAL": 26, "SYNTHETIC": 25}),
        human_inspection_section(
            run_id=RUN_ID,
            sampled=[
                SampledClaim(
                    name="structural detection", inspected=0, defects=0, note="no reference"
                )
            ],
            carried=[
                CarriedClaim(
                    name="every chunk is on the page it names",
                    carried_by="FR-010 total containment check",
                )
            ],
        ),
        chunk_identity_section(run_id=RUN_ID, chunks_minted=6_466),
        exclusion_section(run_id=RUN_ID, scope=scope),
        confidence_section(
            run_id=RUN_ID,
            policy=DECLARED_POLICY,
            distribution=tally_confidence(stored, rejected),
        ),
        failure_breakdown_section(run_id=RUN_ID, counts=outcome_counts(()), attempts=480),
        chunk_count_section(run_id=RUN_ID, profile=profile),
        chunking_section(run_id=RUN_ID, profile=profile),
        page_split_section(
            run_id=RUN_ID,
            counts=MultiChunkCounts(values=120, multi_chunk_values=4, contributing_rows=5),
        ),
        near_duplicate_section(
            run_id=RUN_ID, counts=measure_near_duplicates(vectors), chunks_measured=len(vectors)
        ),
        extraction_quality_section(
            run_id=RUN_ID,
            model_figures=_field_figures(112),
            baseline_figures=_field_figures(104),
            unmeasured_layers={"REAL": "no pre-render document model exists for the real layer"},
        ),
        attempt_ledger_section(
            run_id=RUN_ID,
            invocations=InvocationLedger(valid=40, repaired=6, failed=2),
            attempts=AttemptLedger(attempted=480, stored=430, failed=50),
        ),
        unattempted_fields_section(
            run_id=RUN_ID, printed_counts=PRINTED_COUNTS, attempted_fields=ATTEMPTED_FIELDS
        ),
        reconciliation_section(run_id=RUN_ID, trace_id=TRACE_ID, attempted=48, recorded=48),
        disposition_section(
            run_id=RUN_ID,
            counts={
                "ingested": 25,
                "skipped_unchanged": 25,
                "rolled_back": 1,
                "not_reached": 0,
            },
            enumerated=51,
        ),
        index_procedure_section(run_id=RUN_ID, chunks_resident=6_466),
        encoder_parity_section(run_id=RUN_ID, measurement=measurement),
    ]
    sections.append(total_checks_section(run_id=RUN_ID, checks=collect_total_checks(sections)))
    # Items 19 and 20 are censuses *of* the report's figures, so both are built
    # last and from everything else.
    sections.append(reproduction_section(run_id=RUN_ID, sections=sections))
    sections.append(scope_labels_section(run_id=RUN_ID, sections=sections))
    return sections


def test_build_report_no_longer_refuses_on_a_missing_item() -> None:
    """The assertion T094-T096 exist for.

    Until items 8, 14 and 21 had builders, no set of real sections could be
    complete and `build_report` refused every one of them — which is why the
    committed report could not be regenerated. This is the complete set, from
    real builders only.
    """
    sections = every_section(profile_with(real=4_000, synthetic=2_466), measurement_from_artifact())
    assert sorted(section.item for section in sections) == list(range(1, 22))
    rendered = build_report(sections, run_id=RUN_ID)
    assert "## 8. Chunk counts, total and per layer" in rendered
    assert "## 14. Count of fields printed but not attempted" in rendered
    assert "## 21. Encoder parity bounds declared before the comparison" in rendered


def test_the_complete_report_carries_every_new_items_figures_with_their_labels() -> None:
    """FR-072's five labels reach the three new items too, through item 20."""
    sections = every_section(profile_with(real=4_000, synthetic=2_466), measurement_from_artifact())
    new_items = [section for section in sections if section.item in (8, 14, 21)]
    assert sum(len(section.figures) for section in new_items) > 0
    for section in new_items:
        for figure in section.figures:
            assert figure.scope.run_id == RUN_ID
            assert figure.scope.unit.strip()
            assert figure.tolerance.strip()
