"""What a run publishes about itself: the weighting, the parameters, the figures.

Spec FR-005, FR-006, FR-029. This module exists because several things this
epic must disclose are true of the *corpus* rather than of the code, and a
disclosure that lives only in prose is one nobody can check.

**The lexical arm's field weighting is inert on the synthetic layer, and that is
published rather than assumed away.** `chunk.search_vector` weights heading A,
part numbers B, specification section C and body D. On the synthetic layer
`part_numbers` is NULL on every row, `heading` is NULL on transmittal field
blocks, and `spec_section` is NULL on transmittals because their code appears as
body text — so three of the four weighted arms are empty and only the D-weighted
body contributes. The weighting designed to compensate for PostgreSQL's missing
IDF does nothing exactly where extraction targets. FR-005 requires the
proportion published per layer; E006 owns the repair (`specs/project-plan.md`).

**BM25 is never claimed.** PostgreSQL's ranking functions use no corpus-wide term
statistics — the documentation is explicit that they "do not use any global
information" — so `ts_rank` is not BM25 and BEIR's BM25 numbers do not transfer.
FR-006 makes the disclosure machine-checkable rather than a footnote: the
emitted artifact carries a constant saying so, and `test_report.py` asserts over
emitted output that no figure or label names BM25.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from api.config import RetrievalConfig
from api.retrieval.metrics import IntervalRecord
from api.retrieval.parameters import RankingParameters

__all__ = [
    "LEXICAL_ARM_NAME",
    "USES_CORPUS_WIDE_TERM_STATISTICS",
    "FigureRecord",
    "NoIntervalReason",
    "ReportError",
    "WeightedFieldReport",
    "publish_figure",
    "ranking_parameters_in_force",
    "weighted_field_report",
]


class ReportError(ValueError):
    """A figure cannot be published as offered."""


class NoIntervalReason(StrEnum):
    """Why a figure carries no interval.

    Spec FR-051. `project-instructions.md` v1.2.10 distinguishes an estimated
    proportion from a census: an estimate carries an interval, a census carries
    its exact denominator plus a declaration of why no interval applies — and
    that declaration must name a reason from a **closed set** which the
    publishing artifact declares. An open-ended reason field is satisfied by any
    string and enforces nothing.

    Two values, and no more. Adding a third "just in case" would be the
    open-ended field the clause forbids, arrived at one member at a time.
    """

    #: Computed over every element of a finite, enumerated population — nothing
    #: is sampled, so there is no sampling frame to generalize from. SC-009's
    #: candidate-set figures and SC-016's per-query latency draw on this.
    CENSUS_OVER_ENUMERATED_POPULATION = "census_over_enumerated_population"

    #: Exactly one reading exists for the run, so there is no population to
    #: sample at all. SC-016's per-run resident-memory reading draws on this.
    SINGLE_OBSERVATION = "single_observation"


#: What the sparse arm is called in every emitted figure and label. Named
#: `native tsvector ranking`, never BM25 — see the module docstring.
LEXICAL_ARM_NAME: Final = "native_tsvector_ranking"

#: Machine-readable half of FR-006. A constant rather than prose, so a consumer
#: can branch on it and a test can assert it, instead of both relying on someone
#: having read the note.
USES_CORPUS_WIDE_TERM_STATISTICS: Final = False

#: The three weighted fields that can be empty. `body_text` is NOT NULL in the
#: schema, so it is never among them — which is why "all weighted fields empty"
#: does not mean "the row has no searchable text".
WEIGHTED_FIELDS: Final = ("heading", "part_numbers", "spec_section")


@dataclass(frozen=True)
class WeightedFieldReport:
    """Per-layer proportion of retrieved chunks whose weighted fields are all empty.

    Carries the denominator as well as the proportion, because Principle II's
    census clause requires it: this is computed over every retrieved chunk in
    the run, so it is a census rather than an estimate and publishes its exact
    denominator instead of an interval.
    """

    layer: str
    all_empty: int
    total: int

    @property
    def proportion(self) -> float:
        """The share of retrieved chunks with no weighted field populated."""
        if self.total == 0:
            # A proportion over zero retrieved chunks is undefined rather than
            # zero. Zero would read as "the weighting worked on everything",
            # which is the opposite of what an empty result means.
            msg = f"no chunks were retrieved from layer {self.layer!r}; the proportion is undefined"
            raise ValueError(msg)
        return self.all_empty / self.total


def weighted_field_report(rows: Sequence[Sequence[Any]]) -> list[WeightedFieldReport]:
    """Report, per layer, how often the weighted fields were all empty.

    Each row is `(layer, heading, part_numbers, spec_section)`. Layers with no
    retrieved chunks are omitted rather than reported as zero — see
    `WeightedFieldReport.proportion` for why a zero here would be a false claim.

    Per layer, not corpus-wide, because a corpus-wide figure averages the
    populated public layer together with the empty synthetic one and reports
    something true of neither. That averaging is precisely what would hide the
    defect FR-005 exists to expose.
    """
    tallies: dict[str, list[int]] = {}
    for row in rows:
        if len(row) != 1 + len(WEIGHTED_FIELDS):
            msg = f"expected (layer, {', '.join(WEIGHTED_FIELDS)}), found {len(row)} values"
            raise ValueError(msg)
        layer = str(row[0])
        empty = all(value is None or str(value).strip() == "" for value in row[1:])
        tally = tallies.setdefault(layer, [0, 0])
        tally[0] += 1 if empty else 0
        tally[1] += 1
    return [
        WeightedFieldReport(layer=layer, all_empty=counts[0], total=counts[1])
        for layer, counts in sorted(tallies.items())
    ]


@dataclass(frozen=True)
class FigureRecord:
    """One published figure, with its interval or its reason for having none.

    Spec FR-051. Every figure carries **either** an interval **or** a
    denominator and a reason drawn from a closed set — and the reason is a
    member of `NoIntervalReason`, not a string, because an open-ended reason
    field is satisfied by any text and enforces nothing.
    """

    name: str
    value: float
    interval: tuple[float, float] | None = None
    interval_record: IntervalRecord | None = None
    denominator: int | None = None
    no_interval_reason: NoIntervalReason | None = None
    corpus_size: int | None = None
    ingest_generation: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def publish_figure(record: FigureRecord) -> FigureRecord:
    """Refuse a figure that is not publishable, and return it if it is.

    Spec FR-049 and FR-051, enforced here rather than described elsewhere. Four
    refusals, each for a failure that is otherwise silent:

    - **Neither an interval nor a reason.** A bare point estimate is the false
      confidence Principle II exists to remove.
    - **Both an interval and a no-interval reason.** The figure would claim two
      incompatible things about its own uncertainty.
    - **A census with no denominator.** "No interval applies" is only checkable
      against the population it was computed over; without the denominator the
      declaration is unfalsifiable.
    - **No ingest generation.** FR-049: the repair to `chunk.part_numbers`
      changes no chunk count, so two corpora can report the same size while the
      lexical arm behaves completely differently. A figure qualified only by
      size cannot be told apart from one measured before the repair.
    """
    has_interval = record.interval is not None
    has_reason = record.no_interval_reason is not None
    if not has_interval and not has_reason:
        msg = (
            f"figure {record.name!r} carries neither an interval nor a declared reason "
            f"for having none. A bare point estimate asserts a confidence it has not "
            f"earned, which is what Principle II exists to remove."
        )
        raise ReportError(msg)
    if has_interval and has_reason:
        msg = (
            f"figure {record.name!r} carries both an interval and a no-interval reason; "
            f"it cannot be both an estimate and a census."
        )
        raise ReportError(msg)
    if has_reason and record.denominator is None:
        msg = (
            f"figure {record.name!r} declares {record.no_interval_reason} but names no "
            f"denominator. A census publishes the exact population it covered — without "
            f"it the declaration cannot be checked."
        )
        raise ReportError(msg)
    if record.ingest_generation is None:
        msg = (
            f"figure {record.name!r} names no ingest generation. E006's part_numbers "
            f"repair changes no chunk count, so a corpus-size qualifier alone cannot "
            f"distinguish a pre-repair figure from a post-repair one (FR-049)."
        )
        raise ReportError(msg)
    return record


def ranking_parameters_in_force(config: RetrievalConfig) -> RankingParameters:
    """The parameters a result set was produced under.

    Emitted with **every** response rather than only with results an evaluation
    consumes, because whether a result will be consumed by an evaluation is not
    knowable at the moment it is produced. Built from the live configuration so
    the published figure and the executed query cannot disagree — a constant
    restated here would be a second source of truth for the same number.
    """
    return RankingParameters(
        fetch_depth=config.fetch_depth,
        reranked_count=config.reranked_count,
        search_breadth=config.search_breadth,
        index_mode=config.index_mode,
    )


# ---------------------------------------------------------------------------
# FR-033, FR-041, SC-016: the performance figures and how they were taken
# ---------------------------------------------------------------------------

#: The never-exceed reranking latency, settled at spec §Decisions Taken at
#: Checklist. A single observation above it falsifies SC-016 — chosen over a p95
#: because a p95 across a fifty-query set is decided by its two or three worst
#: observations, which is a weak gate that reads like a strong one.
LATENCY_NEVER_EXCEED_MS: Final = 400.0


@dataclass(frozen=True)
class PerformanceReport:
    """One run's latency and memory, with everything needed to read them.

    FR-033 fixes seven things about how a figure is taken, and all seven travel
    with it. A latency without its workload, environment, measurement point,
    occasion, counter, arm and corpus size is a number that cannot be compared
    with another number — which is the only thing a performance figure is for.
    """

    workload: str
    environment: str
    measurement_point: str
    occasion: str
    counter: str
    arm: str
    corpus_size: int
    ingest_generation: str
    per_query_reranking_ms: tuple[float, ...]
    per_query_fusion_ms: tuple[float, ...]
    per_query_encoder_ms: tuple[float, ...]
    resident_bytes_by_session: dict[str, int]
    process_resident_bytes: int
    peak_resident_bytes: int
    memory_budget_bytes: int

    @property
    def worst_reranking_ms(self) -> float:
        """The never-exceed statistic, which is the gate.

        A **census** over every query in the run — nothing is sampled — so it
        publishes its denominator and no interval, per FR-051.
        """
        if not self.per_query_reranking_ms:
            msg = "no query was timed; the never-exceed figure is undefined"
            raise ReportError(msg)
        return max(self.per_query_reranking_ms)

    @property
    def within_latency_budget(self) -> bool:
        return self.worst_reranking_ms <= LATENCY_NEVER_EXCEED_MS

    @property
    def within_memory_budget(self) -> bool:
        return self.process_resident_bytes <= self.memory_budget_bytes

    def as_figures(self) -> list[FigureRecord]:
        """Every figure this report publishes, each carrying its own uncertainty.

        The memory reading is `single_observation` and the latency is
        `census_over_enumerated_population`: one is a single reading per run
        with no population to sample, the other covers every query. Both are
        censuses in the sense Principle II means, and they draw different
        licensed reasons because they are census for different reasons.
        """
        return [
            publish_figure(
                FigureRecord(
                    name="reranking_latency_never_exceed_ms",
                    value=self.worst_reranking_ms,
                    denominator=len(self.per_query_reranking_ms),
                    no_interval_reason=NoIntervalReason.CENSUS_OVER_ENUMERATED_POPULATION,
                    corpus_size=self.corpus_size,
                    ingest_generation=self.ingest_generation,
                    extra={"budget_ms": LATENCY_NEVER_EXCEED_MS, "arm": self.arm},
                )
            ),
            publish_figure(
                FigureRecord(
                    name="process_resident_bytes",
                    value=float(self.process_resident_bytes),
                    denominator=1,
                    no_interval_reason=NoIntervalReason.SINGLE_OBSERVATION,
                    corpus_size=self.corpus_size,
                    ingest_generation=self.ingest_generation,
                    extra={
                        "budget_bytes": self.memory_budget_bytes,
                        "peak_bytes": self.peak_resident_bytes,
                        # Itemized against the one total rather than apportioned:
                        # FR-033 requires the report break the sessions out, and
                        # the 400 MB is deliberately not split between them.
                        "by_session": dict(self.resident_bytes_by_session),
                    },
                )
            ),
        ]


def degraded_never_exceeds(
    degraded_ms: Sequence[float],
    reranked_ms: Sequence[float],
) -> dict[str, Any]:
    """FR-041. The degraded path's latency, on its own defined terms.

    **The span differs from FR-033's and that is not an oversight.** FR-033
    measures the reranker component's scoring call, and on the degraded path
    that call does not occur — inheriting "the same terms" would leave the span
    empty and the requirement unfalsifiable. The degraded span is total
    in-process query wall-clock, the only span both paths share.

    Two statistics because they answer different questions. The never-exceed is
    the gate. The mean is reported beside it and compared against the reranked
    mean, which is where "removing the reranker made queries faster" is either
    visible or absent — a per-query comparison would be falsified by ordinary
    scheduling jitter on some query in every run.
    """
    if not degraded_ms:
        msg = "no degraded query was timed"
        raise ReportError(msg)
    degraded_mean = sum(degraded_ms) / len(degraded_ms)
    reranked_mean = (sum(reranked_ms) / len(reranked_ms)) if reranked_ms else None
    return {
        "span": "total_in_process_query_wall_clock",
        "never_exceed_ms": max(degraded_ms),
        "within_budget": max(degraded_ms) <= LATENCY_NEVER_EXCEED_MS,
        "mean_ms": degraded_mean,
        "reranked_mean_ms": reranked_mean,
        "faster_than_reranked_on_average": (
            None if reranked_mean is None else degraded_mean <= reranked_mean
        ),
        "denominator": len(degraded_ms),
        "no_interval_reason": str(NoIntervalReason.CENSUS_OVER_ENUMERATED_POPULATION),
    }
