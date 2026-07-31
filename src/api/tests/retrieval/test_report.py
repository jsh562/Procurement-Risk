"""The disclosures a run publishes, asserted over emitted output.

Spec FR-005, FR-006, FR-029. Both requirements here are *disclosure*
requirements, and a disclosure that only exists in prose is one nobody can
check — so every assertion below reads the emitted artifact rather than the
intent behind it.
"""

from __future__ import annotations

import pytest

from api.config import load_retrieval_config
from api.retrieval.metrics import IntervalMethod, wilson_interval
from api.retrieval.parameters import (
    FUSION_CONSTANT,
    MISSING_ARM_CONVENTION,
    TIE_BREAK_KEY,
)
from api.retrieval.report import (
    LEXICAL_ARM_NAME,
    USES_CORPUS_WIDE_TERM_STATISTICS,
    FigureRecord,
    NoIntervalReason,
    ReportError,
    publish_figure,
    ranking_parameters_in_force,
    weighted_field_report,
)

#: `(layer, heading, part_numbers, spec_section)` — the projection FR-005 reads.
PUBLIC_POPULATED = ("REAL", "Pressure Relief Valves", "NRH-80347", "22 05 23")
SYNTHETIC_EMPTY = ("SYNTHETIC", None, None, None)
SYNTHETIC_PARTIAL = ("SYNTHETIC", "A heading", None, None)


def test_the_report_is_per_layer_not_corpus_wide() -> None:
    """One row per layer, because a corpus-wide figure hides the defect.

    Averaging the populated public layer with the empty synthetic one reports
    something true of neither, which is exactly the averaging FR-005 exists to
    prevent.
    """
    reports = weighted_field_report([PUBLIC_POPULATED, SYNTHETIC_EMPTY, SYNTHETIC_EMPTY])
    assert [report.layer for report in reports] == ["REAL", "SYNTHETIC"]


def test_the_synthetic_layer_reports_its_empty_weighting() -> None:
    """The live defect, measured rather than described.

    E006 writes NULL to `part_numbers` on every row, so the weight-B arm is
    empty corpus-wide. This is the figure that says so.
    """
    reports = weighted_field_report([SYNTHETIC_EMPTY, SYNTHETIC_EMPTY])
    assert reports[0].proportion == 1.0
    assert reports[0].all_empty == 2
    assert reports[0].total == 2


def test_a_partially_populated_row_is_not_counted_as_empty() -> None:
    """ "All weighted fields empty" means all three, not any.

    A row with a heading still gets some weighting benefit, so counting it as
    empty would overstate the defect — and a disclosure that overstates is as
    unusable as one that understates.
    """
    reports = weighted_field_report([SYNTHETIC_EMPTY, SYNTHETIC_PARTIAL])
    assert reports[0].all_empty == 1
    assert reports[0].total == 2


def test_the_populated_layer_reports_zero() -> None:
    reports = weighted_field_report([PUBLIC_POPULATED])
    assert reports[0].proportion == 0.0


def test_a_whitespace_only_field_counts_as_empty() -> None:
    """Empty and blank are the same nothing for a text-search weight.

    `to_tsvector` of a blank string produces no lexemes, so a field holding
    spaces contributes exactly as much as a NULL — and counting it as populated
    would understate the defect.
    """
    reports = weighted_field_report([("SYNTHETIC", "   ", "", None)])
    assert reports[0].proportion == 1.0


def test_a_layer_with_no_retrieved_chunks_has_no_proportion() -> None:
    """Undefined, not zero.

    Zero would read as "the weighting worked on everything", which is the
    opposite of what an empty result means.
    """
    reports = weighted_field_report([("REAL", None, None, None)])
    report = reports[0]
    object.__setattr__(report, "total", 0)
    with pytest.raises(ValueError, match="undefined"):
        _ = report.proportion


def test_a_malformed_row_is_refused() -> None:
    with pytest.raises(ValueError, match="expected"):
        weighted_field_report([("REAL", None)])


# ---------------------------------------------------------------------------
# FR-006: the arm is not BM25, and the artifact says so
# ---------------------------------------------------------------------------


def test_the_lexical_arm_is_not_named_bm25() -> None:
    """Asserted over the emitted label, not over intent.

    PostgreSQL's ranking functions use no corpus-wide term statistics, so
    `ts_rank` is not BM25 and BEIR's BM25 numbers do not transfer. A label
    naming it BM25 would import a whole literature's expectations onto a
    different algorithm.
    """
    assert "bm25" not in LEXICAL_ARM_NAME.lower()
    assert LEXICAL_ARM_NAME == "native_tsvector_ranking"


def test_the_no_corpus_statistics_claim_is_machine_readable() -> None:
    """A constant a consumer can branch on, not a note a reader must find."""
    assert USES_CORPUS_WIDE_TERM_STATISTICS is False


def test_no_emitted_identifier_names_bm25() -> None:
    """Sweep every published identifier this module and its parameters expose.

    Cheap, and it catches the label added later by someone who did not read the
    docstring — which is the only way this regresses.
    """
    emitted = [
        LEXICAL_ARM_NAME,
        TIE_BREAK_KEY,
        MISSING_ARM_CONVENTION,
        *[
            str(value)
            for value in ranking_parameters_in_force(load_retrieval_config({})).__dict__.values()
        ],
    ]
    offenders = [value for value in emitted if "bm25" in str(value).lower()]
    assert not offenders, f"emitted identifiers name BM25: {offenders}"


# ---------------------------------------------------------------------------
# FR-029: the parameters in force travel with the results
# ---------------------------------------------------------------------------


def test_the_parameters_come_from_the_live_configuration() -> None:
    """Not restated constants.

    A number restated here would be a second source of truth for the same
    value, and the published figure could then disagree with the executed
    query — which is the disagreement FR-029 exists to make impossible.
    """
    config = load_retrieval_config(
        {"PRC_RETRIEVAL_FETCH_DEPTH": "30", "PRC_RETRIEVAL_RERANKED_COUNT": "30"}
    )
    parameters = ranking_parameters_in_force(config)
    assert parameters.fetch_depth == 30
    assert parameters.reranked_count == 30
    assert parameters.search_breadth == config.search_breadth
    assert parameters.index_mode == config.index_mode


def test_the_three_published_parameters_are_identifier_tokens() -> None:
    """Stable tokens, not prose (FR-004).

    `chunk_id ascending` and `ascending by chunk_id` name one rule and compare
    unequal, so a re-wording would be indistinguishable from a re-tuning to
    anything comparing two runs.
    """
    parameters = ranking_parameters_in_force(load_retrieval_config({}))
    for token in (parameters.tie_break_key, parameters.missing_arm_convention):
        assert token == token.lower()
        assert " " not in token
        assert token.replace("_", "").isalnum()
    assert parameters.fusion_constant == FUSION_CONSTANT == 60


# ---------------------------------------------------------------------------
# FR-049 / FR-051: every figure carries its uncertainty, or says why it has none
# ---------------------------------------------------------------------------


def test_an_estimate_with_an_interval_publishes() -> None:
    lower, upper, record = wilson_interval([True, False, True], with_method=True)
    published = publish_figure(
        FigureRecord(
            name="recall_at_5",
            value=2 / 3,
            interval=(lower, upper),
            interval_record=record,
            corpus_size=6,
            ingest_generation="gen-1",
        )
    )
    assert published.interval_record is not None
    assert published.interval_record.method is IntervalMethod.WILSON


def test_a_census_with_a_denominator_and_a_reason_publishes() -> None:
    published = publish_figure(
        FigureRecord(
            name="truncated_fraction",
            value=0.0,
            denominator=50,
            no_interval_reason=NoIntervalReason.CENSUS_OVER_ENUMERATED_POPULATION,
            ingest_generation="gen-1",
        )
    )
    assert published.no_interval_reason is NoIntervalReason.CENSUS_OVER_ENUMERATED_POPULATION


def test_a_bare_point_estimate_is_refused() -> None:
    """The false confidence Principle II exists to remove."""
    with pytest.raises(ReportError, match="neither an interval nor a declared reason"):
        publish_figure(FigureRecord(name="bare", value=1.0, ingest_generation="gen-1"))


def test_a_figure_claiming_both_is_refused() -> None:
    """It cannot be both an estimate and a census."""
    with pytest.raises(ReportError, match="both"):
        publish_figure(
            FigureRecord(
                name="both",
                value=1.0,
                interval=(0.0, 1.0),
                denominator=1,
                no_interval_reason=NoIntervalReason.SINGLE_OBSERVATION,
                ingest_generation="gen-1",
            )
        )


def test_a_census_without_its_denominator_is_refused() -> None:
    """ "No interval applies" is only checkable against the population covered."""
    with pytest.raises(ReportError, match="denominator"):
        publish_figure(
            FigureRecord(
                name="nodenom",
                value=1.0,
                no_interval_reason=NoIntervalReason.SINGLE_OBSERVATION,
                ingest_generation="gen-1",
            )
        )


def test_a_figure_without_an_ingest_generation_is_refused() -> None:
    """FR-049. The repair changes no chunk count, so size cannot distinguish corpora.

    A figure qualified only by corpus size is indistinguishable from one
    measured before E006's `part_numbers` repair — which is the divorce of a
    number from the corpus it describes that FR-049 exists to prevent.
    """
    with pytest.raises(ReportError, match="ingest generation"):
        publish_figure(FigureRecord(name="nogen", value=1.0, interval=(0.0, 1.0)))


def test_two_figures_differing_only_in_ingest_generation_are_distinguishable() -> None:
    """FR-049's point, stated as the thing that must be *possible*.

    The refusal above proves a figure cannot be published without a generation.
    It does not prove the generation is load-bearing — a field that is required
    but discarded on the way out satisfies the refusal and still leaves the two
    figures identical to a reader. This is the assertion that says otherwise.

    Everything else is held equal on purpose, including `corpus_size`: E006's
    `part_numbers` repair changes no chunk count, so the pre-repair and
    post-repair corpora genuinely report the same size. The generation is the
    *only* member that can tell them apart, and if it fails to, an inert lexical
    weight-B slot and a working one publish the same recall figure.
    """
    before = publish_figure(
        FigureRecord(
            name="recall_at_5",
            value=0.62,
            interval=(0.55, 0.69),
            corpus_size=6391,
            ingest_generation="2026-07-11T00:00:00Z/part-numbers-null",
        )
    )
    after = publish_figure(
        FigureRecord(
            name="recall_at_5",
            value=0.62,
            interval=(0.55, 0.69),
            corpus_size=6391,
            ingest_generation="2026-07-29T00:00:00Z/part-numbers-populated",
        )
    )
    assert before.corpus_size == after.corpus_size
    assert before.value == after.value
    assert before.ingest_generation != after.ingest_generation
    assert before != after, (
        "two figures measured over different ingest generations compare equal; "
        "the generation is carried but not load-bearing, which is FR-049's "
        "failure mode rather than its remedy"
    )


def test_the_generation_survives_publication() -> None:
    """Returned, not merely validated.

    `publish_figure` could satisfy every refusal above and hand back a record
    with the generation stripped; the caller writing the report would then emit
    an unqualified number. Asserted on the returned value for that reason.
    """
    published = publish_figure(
        FigureRecord(
            name="mrr",
            value=0.41,
            interval=(0.33, 0.49),
            ingest_generation="2026-07-29T00:00:00Z/part-numbers-populated",
        )
    )
    assert published.ingest_generation == "2026-07-29T00:00:00Z/part-numbers-populated"


def test_the_no_interval_reason_set_is_closed_at_two() -> None:
    """A closed set, declared by the artifact that publishes the figures.

    v1.2.10 requires the reason come from a closed set *and* that the
    publishing artifact declare it, because an open-ended reason field is
    satisfied by any string. Asserted at two so a third member cannot be added
    without this failing and the addition being argued for.
    """
    assert {reason.value for reason in NoIntervalReason} == {
        "census_over_enumerated_population",
        "single_observation",
    }
