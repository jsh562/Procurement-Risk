"""FR-068 / FR-071 / FR-072: what a published total check has to carry.

FR-068 is one sentence with two obligations, and the second is the one that is
easy to satisfy on paper and lose in code: *publish the population a total check
enumerated and its count*, and *fail rather than report success when that count
is zero*. A "100% of chunks are attributable" that enumerated no chunk is true,
worthless, and indistinguishable from the real thing in a report that prints
only the percentage.

So the checks below are against the **type**, not against a rendered string:
`TotalCheck` refuses an empty population and a zero count at construction, which
makes the vacuous claim unwritable rather than merely discouraged. The same
shape covers FR-072's labels — a `FigureScope` cannot omit the run, the
generation set, the kind, the unit, or the layer — and FR-071's closed content
list, where a missing item stops the report being emitted at all.

**The placeholder sections in this module are placeholders and are labelled as
such.** The remaining items of FR-071's list are owned by US2–US6 tasks that
have not run. They are stubbed here so the *builder* can be exercised over a
complete list; the committed report is generated from real sections by T089 and
nothing here produces it. Items 10 (FR-029, T067) and 20 (FR-072, T074) have
since landed and their real builders replace the stubs — which is what the stubs
were for.
"""

from __future__ import annotations

import numpy as np
import pytest

from model.ingest.chunker import BOUNDARY_CLASSES, Chunk, DocumentChunking
from model.ingest.report import (
    DECLARED_SIMILARITY_GRID,
    FIGURE_KINDS,
    GENERATION_SETS,
    LAYERS,
    NEAR_DUPLICATE_CAUSES,
    REPORT_CONTENTS,
    RULE_OF_THREE_MINIMUM,
    CarriedClaim,
    ChunkVector,
    Figure,
    FigureScope,
    ReportError,
    SampledClaim,
    Section,
    TotalCheck,
    build_report,
    census_of_labels,
    chunk_identity_section,
    chunking_section,
    collect_figures,
    collect_total_checks,
    human_inspection_section,
    measure_near_duplicates,
    near_duplicate_section,
    page_split_section,
    prj_000_section,
    profile_chunkings,
    recognition_error_section,
    scope_labels_section,
    total_checks_section,
)
from model.ingest.writer import MultiChunkCounts

RUN_ID = "00000000-0000-4000-8000-00000000e006"

#: The items US2-US6 own. Named rather than computed as "everything the US1
#: builders do not produce", so a US1 section quietly disappearing shows up as a
#: missing-item failure instead of being absorbed into this set.
PLACEHOLDER_ITEMS = (5, 6, 7, 8, 12, 13, 14, 15, 16, 18, 19, 21)

#: Item 10's counts (FR-029). Four values assembled across a page break and five
#: contributing rows between them, so the row count exceeds the value count and
#: the "three or more pages" case is present rather than assumed away. The real
#: counts come from the run's citations; these exercise the builder.
MULTI_CHUNK_COUNTS = MultiChunkCounts(values=120, multi_chunk_values=4, contributing_rows=5)


def _scope(**overrides: str) -> FigureScope:
    defaults = {
        "run_id": RUN_ID,
        "generation_set": "corpus-resident",
        "kind": "census",
        "unit": "chunk",
        "layer": "pooled",
    }
    defaults.update(overrides)
    return FigureScope(**defaults)  # type: ignore[arg-type]


def _chunk(
    document_id: str,
    ordinal: int,
    *,
    page: int = 1,
    body: str = "Submit shop drawings for the equipment specified in this section.",
    boundary_class: str = "structural",
    identifier: str = "1.1",
    pieces: int = 12,
    heading: str | None = None,
) -> Chunk:
    return Chunk(
        document_id=document_id,
        document_type="specification",
        project_id="PRJ-000",
        page_number=page,
        ordinal=ordinal,
        body_text=body,
        boundary_class=boundary_class,
        structural_identifier=identifier,
        heading=heading,
        content_pieces=pieces,
    )


def _chunking(document_id: str, chunks: list[Chunk]) -> DocumentChunking:
    return DocumentChunking(
        document_id=document_id, chunker_version="e006-chunker/test", chunks=tuple(chunks)
    )


@pytest.fixture(name="profile")
def chunking_profile():
    """A two-layer chunking with one of every boundary class and a fallback page."""
    real = _chunking(
        "ufgs-23-52-00",
        [
            _chunk("ufgs-23-52-00", 0, pieces=40),
            _chunk("ufgs-23-52-00", 1, boundary_class="page_break", pieces=120, page=2),
            _chunk("ufgs-23-52-00", 2, boundary_class="sentence", identifier="2.4.7", pieces=250),
            _chunk("ufgs-23-52-00", 3, boundary_class="sentence", identifier="2.4.7", pieces=90),
            _chunk("ufgs-23-52-00", 4, identifier="p3-body0", page=3, pieces=15),
        ],
    )
    synthetic = _chunking(
        "prj-001-t0001-r0",
        [
            _chunk("prj-001-t0001-r0", 0, pieces=30),
            _chunk("prj-001-t0001-r0", 1, pieces=25),
        ],
    )
    return profile_chunkings([("REAL", real), ("SYNTHETIC", synthetic)])


# ---------------------------------------------------------------------------
# FR-068 — the two obligations, as a type
# ---------------------------------------------------------------------------


def test_a_total_check_over_an_empty_population_cannot_be_constructed() -> None:
    """FR-068's second obligation: zero enumerated is a failure, not a pass."""
    with pytest.raises(ReportError, match="FR-068"):
        TotalCheck(
            name="Every chunk is on the page it names",
            population="every chunk of the resident generation set",
            count=0,
            scope=_scope(),
        )


def test_a_total_check_without_its_population_cannot_be_constructed() -> None:
    """FR-068's first obligation: the population is published, not implied."""
    with pytest.raises(ReportError, match="FR-068"):
        TotalCheck(
            name="Every chunk is on the page it names",
            population="  ",
            count=51,
            scope=_scope(),
        )


def test_a_negative_count_is_refused_as_well_as_a_zero_one() -> None:
    """The boundary either side of zero, so `> 0` is not written as `!= 0`."""
    with pytest.raises(ReportError, match="FR-068"):
        TotalCheck(name="chunks", population="every chunk", count=-1, scope=_scope())
    assert TotalCheck(name="chunks", population="every chunk", count=1, scope=_scope()).count == 1


def test_every_total_check_a_built_report_publishes_names_its_population_and_count(
    profile,
) -> None:
    """The obligation over the report as a whole, not over one constructor call.

    Reaching into the rendered sections rather than trusting the type is the
    point: a section could publish a total check it built and then render a
    percentage without it, and this is what would notice.
    """
    sections = _all_sections(profile)
    checks = collect_total_checks(sections)
    assert checks, "FR-068: a report claiming nothing total has no basis for any '100%' claim"
    for check in checks:
        assert check.population.strip()
        assert check.count > 0
        assert check.scope.kind == "census", (
            "FR-072: a total check is a census and carries no interval"
        )

    rendered = build_report(sections, run_id=RUN_ID)
    for check in checks:
        assert check.population in rendered, f"{check.name!r} publishes no population in the report"
        assert str(check.count) in rendered


def test_the_census_of_total_checks_is_collected_from_the_sections(profile) -> None:
    """Item 17 is assembled from the sections, not from a second hand-kept list.

    A hand-maintained list is how a check ends up published in one section and
    absent from the census that is supposed to enumerate every check.
    """
    sections = [section for section in _all_sections(profile) if section.item != 17]
    checks = collect_total_checks(sections)
    census = total_checks_section(run_id=RUN_ID, checks=checks)
    assert {check.name for check in census.total_checks} == {check.name for check in checks}


def test_a_report_publishing_no_total_check_is_refused() -> None:
    with pytest.raises(ReportError, match="FR-068"):
        total_checks_section(run_id=RUN_ID, checks=[])


# ---------------------------------------------------------------------------
# FR-071 — the closed content list, in both directions
# ---------------------------------------------------------------------------


def test_the_content_list_is_the_twenty_one_items_the_requirement_fixes() -> None:
    assert len(REPORT_CONTENTS) == 21
    assert [item.number for item in REPORT_CONTENTS] == list(range(1, 22))
    assert all(item.obliged_by for item in REPORT_CONTENTS), "every item names what obliges it"


def test_a_missing_item_stops_the_report_being_emitted(profile) -> None:
    """A list entry with nothing under it is a defect in the report."""
    sections = [section for section in _all_sections(profile) if section.item != 9]
    with pytest.raises(ReportError, match="FR-071") as raised:
        build_report(sections, run_id=RUN_ID)
    assert "FR-053" in str(raised.value), "the failure names what the missing item was obliged by"


def test_an_item_outside_the_list_is_refused(profile) -> None:
    """An item in the report but absent from the list is a defect in the list."""
    with pytest.raises(ReportError, match="FR-071"):
        Section(item=99, body="something nobody asked for")


def test_an_item_with_an_empty_body_is_refused() -> None:
    with pytest.raises(ReportError, match="FR-071"):
        Section(item=4, body="   \n  ")


def test_the_same_item_supplied_twice_is_refused(profile) -> None:
    sections = _all_sections(profile)
    with pytest.raises(ReportError, match="FR-071"):
        build_report([*sections, sections[0]], run_id=RUN_ID)


def test_the_report_names_the_run_it_describes(profile) -> None:
    rendered = build_report(_all_sections(profile), run_id=RUN_ID)
    assert RUN_ID in rendered
    with pytest.raises(ReportError, match="FR-072"):
        build_report(_all_sections(profile), run_id="  ")


# ---------------------------------------------------------------------------
# FR-072 — the five labels, none of them optional
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", " "),
        ("generation_set", "whenever"),
        ("kind", "estimate"),
        ("unit", ""),
        ("layer", "REAL_LAYER"),
    ],
)
def test_a_figure_scope_missing_or_misnaming_any_label_is_refused(field: str, value: str) -> None:
    with pytest.raises(ReportError, match="FR-072"):
        _scope(**{field: value})


def test_every_figure_in_a_built_report_carries_all_five_labels(profile) -> None:
    """FR-072, over the whole report rather than over one constructor call.

    Enumerated through `collect_figures` — the same function item 20's census
    uses — so the population this asserts over and the population the report
    publishes a count of cannot drift apart.
    """
    figures = collect_figures(_all_sections(profile))
    assert figures, "the US1 sections publish figures"
    for figure in figures:
        assert figure.scope.run_id == RUN_ID
        assert figure.scope.generation_set in GENERATION_SETS
        assert figure.scope.kind in FIGURE_KINDS
        assert figure.scope.layer in LAYERS
        assert figure.scope.unit.strip()
        assert figure.label.strip()


def test_a_figure_without_a_label_is_refused() -> None:
    with pytest.raises(ReportError):
        Figure(label="", value=1, scope=_scope())


# ---------------------------------------------------------------------------
# FR-011 — the enumerated claim set and its fixed bound method
# ---------------------------------------------------------------------------


def test_a_zero_defect_claim_above_thirty_quotes_the_rule_of_three() -> None:
    claim = SampledClaim(name="label recognition", inspected=100, defects=0)
    assert "rule of three" in claim.method
    assert "0.0300" in claim.bound


def test_the_rule_of_three_is_never_quoted_at_or_below_thirty() -> None:
    """FR-011 states the exclusion explicitly, so the boundary is asserted."""
    at_boundary = SampledClaim(name="structure detection", inspected=30, defects=0)
    assert "none quoted" in at_boundary.method
    assert "no bound" in at_boundary.bound
    above = SampledClaim(name="structure detection", inspected=31, defects=0)
    assert "rule of three" in above.method
    assert str(RULE_OF_THREE_MINIMUM) in at_boundary.method


def test_a_claim_with_nothing_inspected_is_published_with_no_bound() -> None:
    """The known member of the enumeration, and the one most likely to be dropped."""
    claim = SampledClaim(
        name="structural detection on the 26 real specifications", inspected=0, defects=0
    )
    assert claim.bound == "no bound — nothing has been inspected"


def test_a_claim_with_defects_names_the_wilson_interval() -> None:
    """One or more defects selects FR-060's interval, named before it is computed."""
    claim = SampledClaim(name="label recognition", inspected=50, defects=2)
    assert "Wilson" in claim.method
    assert "Wilson" in claim.bound


def test_more_defects_than_inspected_items_is_refused() -> None:
    with pytest.raises(ReportError, match="FR-011"):
        SampledClaim(name="label recognition", inspected=3, defects=4)


def test_an_empty_claim_enumeration_is_refused() -> None:
    """Both halves of the enumeration are required, and each fails on its own."""
    carried = [CarriedClaim(name="page attribution", carried_by="FR-010 containment check")]
    with pytest.raises(ReportError, match="FR-011"):
        human_inspection_section(run_id=RUN_ID, sampled=[], carried=carried)

    sampled = [SampledClaim(name="structural detection", inspected=0, defects=0)]
    with pytest.raises(ReportError, match="FR-011"):
        human_inspection_section(run_id=RUN_ID, sampled=sampled, carried=[])


# ---------------------------------------------------------------------------
# FR-053 — the measured chunking profile
# ---------------------------------------------------------------------------


def test_the_profile_publishes_every_figure_per_layer_and_pooled(profile) -> None:
    assert set(profile.by_layer) == set(LAYERS)
    pooled = profile.by_layer["pooled"]
    assert pooled.chunks == profile.by_layer["REAL"].chunks + profile.by_layer["SYNTHETIC"].chunks
    assert pooled.documents == 2


def test_a_boundary_class_holding_nothing_is_published_as_a_zero(profile) -> None:
    synthetic = profile.by_layer["SYNTHETIC"]
    assert set(synthetic.boundary_class_counts) == set(BOUNDARY_CLASSES)
    assert synthetic.boundary_class_counts["sentence"] == 0
    assert synthetic.boundary_class_counts["page_break"] == 0


def test_a_leaf_split_into_several_chunks_counts_once(profile) -> None:
    """FR-053 counts *leaves requiring a split*, not the chunks the split made."""
    real = profile.by_layer["REAL"]
    assert real.boundary_class_counts["sentence"] == 2
    assert real.leaves_split_into_sentences == 1


def test_the_page_terminal_fallback_is_published_per_document(profile) -> None:
    real = profile.by_layer["REAL"]
    assert real.page_terminal_chunks_by_document == {"ufgs-23-52-00": 1}
    assert real.page_terminal_documents == 1
    assert profile.by_layer["SYNTHETIC"].page_terminal_documents == 0


def test_percentiles_are_nearest_rank_one_based_without_interpolation(profile) -> None:
    """The convention `schema_constants.percentile_convention` publishes.

    Asserted against a hand-computed value rather than against NumPy, whose
    default interpolates and would give 105.0 for the median of this
    distribution where nearest rank gives 90.
    """
    real = profile.by_layer["REAL"]
    assert sorted(real.leaf_lengths) == [15, 40, 90, 120, 250]
    assert real.percentiles[50] == 90
    assert real.percentiles[99] == 250


def test_an_empty_chunking_profile_is_refused() -> None:
    with pytest.raises(ReportError, match="FR-053"):
        profile_chunkings([])


def test_a_layer_outside_the_corpus_two_is_refused() -> None:
    with pytest.raises(ReportError, match="FR-053"):
        profile_chunkings([("SCANNED", _chunking("x", [_chunk("x", 0)]))])


# ---------------------------------------------------------------------------
# FR-061 — the declared grid, and a curve rather than a point
# ---------------------------------------------------------------------------


def _vector(seed: float, dimension: int = 8) -> np.ndarray:
    base = np.full(dimension, seed, dtype=np.float32)
    base[0] = 1.0
    return base / np.linalg.norm(base)


def _chunk_vector(
    document_id: str, ordinal: int, *, layer: str, heading, text, seed
) -> ChunkVector:
    return ChunkVector(
        document_id=document_id,
        layer=layer,
        ordinal=ordinal,
        page_number=1,
        heading=heading,
        body_text=text,
        embedding=_vector(seed),
    )


@pytest.fixture(name="vectors")
def near_duplicate_vectors() -> tuple[ChunkVector, ...]:
    """Two identical reference lists, one resubmittal pair, no agency variant.

    The third cause has no candidate pair in this fixture on purpose: FR-061
    requires a cause with no cluster to be published as a zero rather than
    dropped, and a fixture in which all three fire could not show that.
    """
    shared = "ASTM A123 (2020) Zinc Coatings\nASTM B633 (2019) Electrodeposited Coatings"
    return (
        _chunk_vector(
            "ufgs-23-52-00", 0, layer="REAL", heading="REFERENCES", text=shared, seed=0.1
        ),
        _chunk_vector(
            "ufgs-26-05-13", 0, layer="REAL", heading="REFERENCES", text=shared, seed=0.1
        ),
        _chunk_vector(
            "prj-001-t0004-r0",
            0,
            layer="SYNTHETIC",
            heading=None,
            text="Submittal Number: T0004",
            seed=0.2,
        ),
        _chunk_vector(
            "prj-001-t0004-r1",
            0,
            layer="SYNTHETIC",
            heading=None,
            text="Submittal Number: T0004",
            seed=0.2,
        ),
        _chunk_vector(
            "prj-002-t0001-r0",
            0,
            layer="SYNTHETIC",
            heading=None,
            text="Submittal Number: T0001",
            seed=0.9,
        ),
    )


def test_the_grid_is_the_five_declared_thresholds() -> None:
    assert DECLARED_SIMILARITY_GRID == (0.80, 0.85, 0.90, 0.95, 0.99)


def test_all_three_causes_are_published_including_one_with_no_candidate(vectors) -> None:
    counts = measure_near_duplicates(vectors)
    assert [entry.cause for entry in counts] == [cause for cause, _ in NEAR_DUPLICATE_CAUSES]
    agency = next(entry for entry in counts if entry.cause == NEAR_DUPLICATE_CAUSES[1][0])
    assert agency.exact_clusters == 0
    assert set(agency.clusters_by_threshold.values()) == {0}
    assert all(entry.candidate_rule for entry in counts), "each cause publishes its declared rule"


def test_the_count_is_published_at_every_threshold_as_a_curve(vectors) -> None:
    counts = measure_near_duplicates(vectors)
    for entry in counts:
        assert set(entry.clusters_by_threshold) == set(DECLARED_SIMILARITY_GRID)
        ordered = [entry.clusters_by_threshold[t] for t in sorted(DECLARED_SIMILARITY_GRID)]
        assert ordered == sorted(ordered, reverse=True), (
            f"{entry.cause}: raising the threshold cannot add a cluster edge, so the curve "
            f"is non-increasing; got {ordered}"
        )


def test_exact_matches_are_counted_on_normalized_text(vectors) -> None:
    counts = measure_near_duplicates(vectors)
    references = next(entry for entry in counts if entry.cause == NEAR_DUPLICATE_CAUSES[0][0])
    assert references.candidates == 2
    assert references.exact_clusters == 1

    resubmittals = next(entry for entry in counts if entry.cause == NEAR_DUPLICATE_CAUSES[2][0])
    assert resubmittals.candidates == 2, (
        "prj-002-t0001-r0 shares its revision stem with no other document, so it can form no "
        "pair and is not a candidate"
    )
    assert resubmittals.exact_clusters == 1, "the R0/R1 pair of one chain"


def test_a_cluster_is_a_component_not_a_pair() -> None:
    """Five identical chunks are one cluster, not ten near-duplicate pairs."""
    same = "ASTM A123 (2020) Zinc Coatings"
    members = tuple(
        _chunk_vector(
            f"ufgs-26-0{index}-13", 0, layer="REAL", heading="REFERENCES", text=same, seed=0.1
        )
        for index in range(1, 6)
    )
    counts = measure_near_duplicates(members)
    references = next(entry for entry in counts if entry.cause == NEAR_DUPLICATE_CAUSES[0][0])
    assert references.candidates == 5
    assert references.exact_clusters == 1


def test_an_empty_chunk_population_is_refused_rather_than_reporting_zero_clusters() -> None:
    """FR-068 again: zero clusters from nothing is not zero clusters."""
    with pytest.raises(ReportError, match="FR-068"):
        measure_near_duplicates([])


# ---------------------------------------------------------------------------
# Section assembly
# ---------------------------------------------------------------------------


def _all_sections(profile) -> list[Section]:
    """Every item of the closed list: the US1 sections, plus labelled stubs.

    The stubs are for items US2-US6 own and each says so in its body. They exist
    so `build_report` can be exercised over a complete list; the committed
    report is not produced here and is not produced from these.
    """
    vectors = (
        _chunk_vector(
            "ufgs-23-52-00", 0, layer="REAL", heading="REFERENCES", text="ASTM A123", seed=0.1
        ),
        _chunk_vector(
            "ufgs-26-05-13", 0, layer="REAL", heading="REFERENCES", text="ASTM A123", seed=0.1
        ),
    )
    sections = [
        prj_000_section(run_id=RUN_ID, real_documents=26),
        recognition_error_section(run_id=RUN_ID, documents_by_layer={"REAL": 26, "SYNTHETIC": 25}),
        human_inspection_section(
            run_id=RUN_ID,
            sampled=[
                SampledClaim(
                    name="structural detection on the 26 real specifications",
                    inspected=0,
                    defects=0,
                    note="no reference exists for UFGS structure",
                )
            ],
            carried=[
                CarriedClaim(
                    name="every chunk is on the page it names",
                    carried_by="FR-010 total containment check",
                )
            ],
        ),
        chunk_identity_section(run_id=RUN_ID, chunks_minted=6466),
        chunking_section(run_id=RUN_ID, profile=profile),
        near_duplicate_section(
            run_id=RUN_ID, counts=measure_near_duplicates(vectors), chunks_measured=len(vectors)
        ),
    ]
    # Item 10 is US4's and is real (T067), so it joins the census of total
    # checks below rather than being stubbed past it.
    sections.append(page_split_section(run_id=RUN_ID, counts=MULTI_CHUNK_COUNTS))
    sections.append(total_checks_section(run_id=RUN_ID, checks=collect_total_checks(sections)))
    sections.extend(
        Section(
            item=item,
            body=(
                f"Not yet published. Item {item} is owned by a US2-US6 task and this is a "
                f"placeholder used only to exercise the builder in the test suite."
            ),
        )
        for item in PLACEHOLDER_ITEMS
    )
    # Item 20 is built last and from everything else: it is a census *of* the
    # report's figures, so it cannot be assembled before they exist.
    sections.append(scope_labels_section(run_id=RUN_ID, sections=sections))
    return sections


def test_the_us1_sections_render_into_a_complete_report(profile) -> None:
    rendered = build_report(_all_sections(profile), run_id=RUN_ID)
    for item in REPORT_CONTENTS:
        assert f"## {item.number}. {item.title}" in rendered
    assert "PRJ-000" in rendered
    assert "no recognition step is performed" in rendered
    assert "minted by the run that writes it" in rendered
    assert "nearest rank" in rendered


# ---------------------------------------------------------------------------
# FR-072 — item 20's census of the labels, and the run identifier it enforces
# ---------------------------------------------------------------------------


def test_item_twenty_counts_every_figure_the_other_sections_publish(profile) -> None:
    """T074. The census is built **from** the sections rather than beside them.

    A hand-kept inventory is how a figure ends up in the report without
    appearing in the one place its labels would have been checked, which is the
    same defect item 17 avoids for total checks.
    """
    sections = _all_sections(profile)
    others = [section for section in sections if section.item != 20]
    counted = sum(len(section.figures) for section in others)
    census = scope_labels_section(run_id=RUN_ID, sections=others)
    assert census.item == 20
    assert census.total_checks[0].count == counted
    assert counted > 0, "FR-068: a labelling census over no figure passes vacuously"


def test_the_label_census_publishes_a_zero_rather_than_omitting_a_kind(profile) -> None:
    """A kind nothing took is a zero row, not an absent one.

    An omitted row and a zero row read the same to a reader and only one of them
    is a measurement — FR-034's rule, applied to the closed vocabularies FR-072
    fixes. The counting **unit** is deliberately not treated this way: it is not
    a closed set, and enumerating one would force a leaf-length figure into a
    unit it does not have.
    """
    sections = [section for section in _all_sections(profile) if section.item != 20]
    census = census_of_labels(collect_figures(sections))
    assert set(census.by_kind) == set(FIGURE_KINDS)
    assert set(census.by_generation_set) == set(GENERATION_SETS)
    assert set(census.by_layer) == set(LAYERS)
    assert sum(census.by_kind.values()) == census.figures
    assert sum(census.by_unit.values()) == census.figures

    rendered = scope_labels_section(run_id=RUN_ID, sections=sections).render()
    for kind in FIGURE_KINDS:
        assert f"| {kind} | {census.by_kind[kind]} |" in rendered


def test_a_figure_computed_under_another_run_is_refused(profile) -> None:
    """The defect item 20 exists to catch, in both places that can catch it.

    A figure carried over from a previous run reads as this run's work and every
    label on it is correct except the one that decides whether the number
    describes what the report says it does. `scope_labels_section` refuses it,
    and so does `build_report` — so a report assembled by hand rather than
    through the census is refused too.
    """
    sections = [section for section in _all_sections(profile) if section.item != 20]
    stale = Section(
        item=20,
        body="a section carrying a figure from an earlier run",
        figures=(
            Figure(
                label="Chunks minted",
                value=6466,
                scope=_scope(run_id="00000000-0000-4000-8000-00000000dead"),
            ),
        ),
    )
    with pytest.raises(ReportError, match="FR-072"):
        scope_labels_section(run_id=RUN_ID, sections=[*sections, stale])
    with pytest.raises(ReportError, match="FR-072"):
        build_report([*sections, stale], run_id=RUN_ID)


def test_a_labelling_census_over_no_figure_is_refused() -> None:
    """FR-068 reaching the one figure that would otherwise be vacuously true."""
    with pytest.raises(ReportError, match="FR-072"):
        census_of_labels([])
