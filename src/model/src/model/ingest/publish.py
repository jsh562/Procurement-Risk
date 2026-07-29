"""The report driver: the caller `build_report` did not have until now.

FR-071 and the twenty-two requirements whose publish obligation runs through it
— FR-003, FR-009, FR-011, FR-018, FR-022, FR-029, FR-033, FR-034, FR-046,
FR-050, FR-053, FR-057, FR-058, FR-060, FR-061, FR-064, FR-068, FR-069, FR-070,
FR-071, FR-072, FR-073, FR-074.

**Every piece this module drives was built, tested, and had no production
caller.** `report.py` is 3,977 lines with twenty-one section builders;
`build_report` had no caller anywhere outside tests, `REPORT_PATH` was declared
and never written — `report.py` says in as many words that "this module writes
no file" — and `results_manifest` had zero call sites. `cli.main` ended at four
`print` statements. This is the third instance of that shape in this epic
(`extract.py` and `cli.run_ingestion` were the first two), and it is the same
defect each time: components complete, tested in isolation, nothing calling them
in order.

**The sections are assembled from the run's own data and from queries against
what it wrote.** Nothing here is a literal standing in for a measurement. The
one exception is item 3's enumerated claim set, which is not a measurement at
all: FR-011 requires the claims resting on human inspection to be enumerated
with their inspected counts, and the honest count for this epic is **zero**,
published as a zero rather than omitted. It is declared here, beside the code
that publishes it, so that changing it means editing the enumeration rather than
editing a number.

**A partial run does not silently produce no report** (FR-071, Principle VII).
`build_report` refuses an incomplete content list, and that refusal is correct —
a short report is a report making a claim it did not publish the basis for. What
was missing is that the refusal reached nobody. With no fixtures committed
(T081) a `replay` run aborts at the first transmittal, so the items needing a
stored extracted value have no data and the report is not emitted; that outcome
is now **named** — which items, what each is obliged by, and why each has no
data, and what stopped the run — and returned on the run's outcome for the entry
to print. Silence and refusal look identical from outside, and only one of them
is a decision.

**Most of the list needs no extracted value at all**, and those items are
genuinely assembled here rather than skipped once the refusal is known: the
enumeration, the chunk counts and the leaf-length distribution, the exclusion,
the near-duplicate clusters over the vectors the run committed, the printed
fields nobody attempted, the invocation reconciliation, the disposition ledger,
the index window, the encoder parity, and the three censuses built from the
others. **Measured on the committed corpus**: a fixture-blocked full `replay`
run builds **16 of the 21** and names 5 gaps — items 6, 7, 10, 12 and 13, each
denominated on a value the run never stored. A driver that saw the first gap and
stopped would report the gap correctly and would never have exercised the
sixteen, which is how this layer came to have no caller in the first place.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from model.compute.metrics import per_field_figures
from model.corpus.manifest import LAYER_REAL
from model.ingest.chunker import DocumentChunking
from model.ingest.documents import DocumentRecord
from model.ingest.failures import outcome_counts
from model.ingest.report import (
    REPORT_CONTENTS,
    REPORT_PATH,
    RESULTS_MANIFEST_PATH,
    AttemptLedger,
    CarriedClaim,
    InvocationLedger,
    ReportError,
    SampledClaim,
    Section,
    attempt_ledger_section,
    build_report,
    chunk_count_section,
    chunk_identity_section,
    chunking_section,
    collect_total_checks,
    confidence_section,
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
    read_resident_chunks,
    recognition_error_section,
    reconciliation_section,
    reproduction_section,
    results_manifest,
    scope_labels_section,
    tally_confidence,
    total_checks_section,
    unattempted_fields_section,
)
from model.ingest.runs import ConfidencePolicy
from model.ingest.writer import multi_chunk_counts

if TYPE_CHECKING:  # pragma: no cover - a type reference, not a dependency
    # `model.ingest.extract` reaches `model.llm.extraction` and thence the
    # gateway's request types. This module needs the *shape* of a stage result
    # and none of its behaviour, so the reference is a type reference and the
    # attributes below are read directly rather than through `getattr` defaults
    # — a default would turn a renamed field into a report of zeros.
    from model.ingest.extract import ExtractionStageResult

__all__ = [
    "CARRIED_CLAIMS",
    "SAMPLED_CLAIMS",
    "ItemGap",
    "PublicationOutcome",
    "RunEvidence",
    "publish_report",
]


#: FR-011's enumerated claim set, with the count each rests on.
#:
#: **Zero is the honest number and it is published as a zero.** No human
#: inspected any structural detection on the 26 real specifications, and the
#: requirement's own rule is that a claim with an inspected count of zero appears
#: with that count rather than being left out — "a claim omitted because nobody
#: inspected anything is the failure mode this enumeration exists to make
#: visible". `SampledClaim.bound` then declines to quote a rule-of-three bound
#: below its declared minimum, so the row reads "no bound — nothing has been
#: inspected", which is the truth and is not flattering.
#:
#: Declared here rather than passed in by the caller: FR-011 fixes the *method*
#: before the counts exist, and an enumeration a caller assembles is one a caller
#: can narrow to the claims that scored well.
SAMPLED_CLAIMS: tuple[SampledClaim, ...] = (
    SampledClaim(
        name="Structural detection on the real layer",
        inspected=0,
        defects=0,
        note=(
            "no human has inspected a detected article, paragraph or subparagraph against "
            "the page it was detected on; the claim is carried by no sample"
        ),
    ),
    SampledClaim(
        name="Page attribution of a chunk to the page it prints",
        inspected=0,
        defects=0,
        note="carried by FR-010's total containment check rather than by a sample",
    ),
)

#: The claims carried by a total check rather than by inspection (FR-011). Each
#: names the check that discharges it, so the enumeration above can be read as
#: complete rather than as the claims someone remembered to sample.
CARRIED_CLAIMS: tuple[CarriedClaim, ...] = (
    CarriedClaim(
        name="Every chunk is on the page it names",
        carried_by="FR-010's total page-containment check, run per document inside its transaction",
    ),
    CarriedClaim(
        name="Every enumerated document carries exactly one disposition",
        carried_by="FR-073's disposition ledger, whose sum is asserted at construction (item 16)",
    ),
    CarriedClaim(
        name="Every attempt resolves to a stored value, a failure, or a correct negative",
        carried_by="FR-069's attempt ledger and its published unaccounted count (item 13)",
    ),
    CarriedClaim(
        name="Every published figure carries its five scope labels",
        carried_by="FR-072's census of labels over every figure in this report (item 20)",
    ),
    CarriedClaim(
        name="Every published figure names the tolerance it reproduces within",
        carried_by="FR-074's census of reproduction tolerances (item 19)",
    ),
)


@dataclass(frozen=True)
class ItemGap:
    """One content item that could not be built, and why.

    `reason` is the refusal as its builder stated it — the `ReportError` message,
    not a paraphrase. A paraphrase written here would be a second account of a
    refusal whose whole content is the first one.
    """

    item: int
    title: str
    obliged_by: tuple[str, ...]
    reason: str

    def rendered(self) -> str:
        return f"item {self.item} ({self.title}; {', '.join(self.obliged_by)}): {self.reason}"


@dataclass
class RunEvidence:
    """Everything the report is built from, collected as the run produces it.

    Mutable and accumulated, because the run produces it in pieces and in an
    order the write order fixes: the chunkings arrive one document ahead of the
    write, the extraction results arrive inside each document's transaction, and
    the resident vectors exist only after the last commit.

    **`chunkings` carries the layer beside each chunking** because FR-053
    requires every figure per layer and a `DocumentChunking` carries no layer of
    its own.
    """

    chunkings: list[tuple[str, DocumentChunking]] = field(default_factory=list)

    def record(self, record: DocumentRecord, chunking: DocumentChunking) -> None:
        self.chunkings.append((record.source_kind, chunking))


@dataclass(frozen=True)
class PublicationOutcome:
    """What the report driver did, whether or not it emitted anything.

    Both paths are values rather than one being an absence. A driver that
    returned `None` when it refused would be indistinguishable from a driver
    nobody called, which is the exact defect this module exists to close.
    """

    run_id: str
    items_published: tuple[int, ...]
    gaps: tuple[ItemGap, ...]
    report_path: Path | None = None
    manifest_path: Path | None = None
    aborted_at: str | None = None

    @property
    def emitted(self) -> bool:
        return self.report_path is not None

    def rendered(self) -> str:
        """The outcome as one line for the console, emitted or refused.

        The refusal names the items, what obliges each, and why each has no
        data — which is what makes it a published outcome rather than a message
        saying something went wrong.
        """
        if self.emitted:
            return (
                f"report: emitted {len(self.items_published)} of {len(REPORT_CONTENTS)} items "
                f"to {self.report_path} and the results manifest to {self.manifest_path}"
            )
        aborted = (
            f" because the run aborted at {self.aborted_at}"
            if self.aborted_at
            else " because the run produced no data for them"
        )
        return (
            f"report not emitted: {len(self.gaps)} of {len(REPORT_CONTENTS)} items have no "
            f"data{aborted}. FR-071's content list is closed and a short report is a report "
            f"making a claim it did not publish the basis for, so nothing is written. The "
            f"{len(self.items_published)} items that could be built were built. Missing: "
            + "; ".join(gap.rendered() for gap in self.gaps)
        )


def _title(item: int) -> tuple[str, tuple[str, ...]]:
    entry = next(entry for entry in REPORT_CONTENTS if entry.number == item)
    return entry.title, entry.obliged_by


class _Assembler:
    """Builds sections one at a time, recording a refusal instead of raising.

    A refusal from one builder must not stop the other twenty: the gap list is
    only worth publishing if it is the *complete* list of what could not be
    built, and a driver that stopped at the first would name one item and imply
    the rest were fine.

    **Every exception becomes a gap, not only the declared refusals.** The
    builders raise `ReportError` and `MetricsError` by design, but they also
    reach a database driver, an ONNX session and a PDF reader, and this step runs
    *after* the run's documents are committed and its record closed. An escaping
    exception here would turn a completed run into a caller-visible failure over
    a report — the wrong order, since the rows are durable either way. Nothing is
    swallowed: the exception's type and message become the gap's reason, and the
    refusal that names it is printed.
    """

    def __init__(self) -> None:
        self.sections: list[Section] = []
        self.gaps: list[ItemGap] = []

    def add(self, item: int, build) -> None:  # noqa: ANN001 - a nullary section builder
        title, obliged_by = _title(item)
        try:
            self.sections.append(build())
        except Exception as error:  # noqa: BLE001 - a failed item is a named gap, not an abort
            self.gaps.append(
                ItemGap(
                    item=item,
                    title=title,
                    obliged_by=obliged_by,
                    reason=f"{type(error).__name__}: {error}",
                )
            )


def publish_report(
    connection: object,
    *,
    run_id: str,
    trace_id: str,
    records: Sequence[DocumentRecord],
    layers: Mapping[str, int],
    exclusion: Section,
    disposition_counts: Mapping[str, int],
    enumerated: int,
    evidence: RunEvidence,
    extractions: Sequence[ExtractionStageResult],
    attempted_fields: Sequence[str],
    policy: ConfidencePolicy,
    attempted_invocations: int,
    attempted_extractions: int,
    aborted_at: str | None = None,
    root: Path | None = None,
) -> PublicationOutcome:
    """Build all twenty-one items from this run's data, and write or refuse.

    Args:
        connection: the run's connection, used for the two queries the report's
            corpus-resident figures rest on — the resident chunks with their
            vectors (item 11, item 18) and the invocation rows carrying this
            run's trace identifier (item 15).
        run_id: the run record this report describes (FR-072).
        trace_id: the run's one trace identifier (FR-070).
        records: every enumerated document, both layers.
        layers: document counts per layer, from `cli.documents_by_layer` — the
            layer is a fact about the enumerated corpus, which that module owns.
        exclusion: item 5, already built by `cli.exclusion_section` — that
            function owns the partition and this module publishes what it is
            given.
        disposition_counts: FR-073's four, from the run's ledger.
        enumerated: the ledger's population.
        evidence: the chunkings the run cut, with their layers.
        extractions: the per-document `ExtractionStageResult`s. Referenced as a
            type only — `model.ingest.extract` reaches `model.llm` and thence
            the gateway's request types, and this module needs the shape of a
            result and none of its behaviour.
        attempted_fields: the subset this run attempted, after the run-time
            retirement filter (FR-024).
        policy: the floor and three weights **this run** recorded.
        attempted_invocations: one per chunk of every attempted document
            (FR-069), derived from the corpus shape by `cli`.
        attempted_extractions: the run's attempt total, from
            `cli.count_attempts`. **Derived from the corpus shape**, while the
            three resolutions are enumerated from what the stage produced — so
            `AttemptLedger.unaccounted` compares two independent derivations
            rather than restating one of them. Summing the resolutions to get
            the total would make the ledger balance by construction and publish
            a zero that measured nothing.
        aborted_at: what stopped the run, where one did. Named in the refusal so
            "these items have no data" carries its cause.
        root: the repository root the two **artifact** paths resolve against.
            `None` resolves from this file, which is where the committed tree
            is; a test passes a temporary directory so a test run cannot rewrite
            the committed report. It is deliberately *not* the corpus root: the
            reference set is always reproduced from the committed corpus, since
            a figure scored against a temporary corpus would be a figure about
            nothing.

    Returns:
        The outcome, emitted or refused, in both cases naming what it did.

    **Nothing here raises on missing data.** Every builder's refusal becomes an
    `ItemGap`, and the decision not to write is taken once, at the end, from the
    complete gap list. A driver that let one refusal escape would abort the run's
    last step over a report, which is the wrong order: the documents are
    committed and durable, and what is at stake is only whether their account can
    be published.
    """
    assembler = _Assembler()
    chunks_minted = sum(len(chunking.chunks) for _, chunking in evidence.chunkings)

    values = [value for result in extractions for value in result.values]
    failures = [entry for result in extractions for entry in result.failures]
    stored_signals = [value.signals for value in values]
    rejected_signals = [signals for result in extractions for signals in result.rejected_signals]
    attempts = _attempt_ledger(extractions, attempted_extractions)
    invocations = InvocationLedger(
        valid=sum(result.invocations_valid for result in extractions),
        repaired=sum(result.invocations_repaired for result in extractions),
        failed=sum(result.invocations_failed for result in extractions),
    )

    resident = _resident_chunks(connection)
    profile = _profile(evidence)
    # Reproduced and digest-verified once, and shared by items 12 and 14. Both
    # need it and building it twice would reproduce and re-verify the whole
    # synthetic layer a second time to answer the same question.
    reference = _Lazy(_build_reference)

    assembler.add(1, lambda: prj_000_section(run_id=run_id, real_documents=layers[LAYER_REAL]))
    assembler.add(2, lambda: recognition_error_section(run_id=run_id, documents_by_layer=layers))
    assembler.add(
        3,
        lambda: human_inspection_section(
            run_id=run_id, sampled=SAMPLED_CLAIMS, carried=CARRIED_CLAIMS
        ),
    )
    assembler.add(4, lambda: chunk_identity_section(run_id=run_id, chunks_minted=chunks_minted))
    assembler.add(5, lambda: exclusion)
    assembler.add(
        6,
        lambda: confidence_section(
            run_id=run_id,
            policy=policy,
            distribution=tally_confidence(stored_signals, rejected_signals),
        ),
    )
    assembler.add(
        7,
        lambda: failure_breakdown_section(
            run_id=run_id,
            counts=outcome_counts(failures),
            attempts=attempts.attempted if attempts else 0,
        ),
    )
    assembler.add(8, lambda: chunk_count_section(run_id=run_id, profile=_require(profile)))
    assembler.add(9, lambda: chunking_section(run_id=run_id, profile=_require(profile)))
    assembler.add(10, lambda: page_split_section(run_id=run_id, counts=multi_chunk_counts(values)))
    assembler.add(
        11,
        lambda: near_duplicate_section(
            run_id=run_id,
            counts=measure_near_duplicates(resident),
            chunks_measured=len(resident),
        ),
    )
    assembler.add(
        12,
        lambda: _quality_section(
            run_id=run_id, records=records, extractions=extractions, reference=reference
        ),
    )
    assembler.add(
        13,
        lambda: attempt_ledger_section(
            run_id=run_id, invocations=invocations, attempts=_require(attempts)
        ),
    )
    assembler.add(
        14,
        lambda: _unattempted_section(
            run_id=run_id, attempted_fields=attempted_fields, reference=reference
        ),
    )
    assembler.add(
        15,
        lambda: reconciliation_section(
            run_id=run_id,
            trace_id=trace_id,
            attempted=attempted_invocations,
            recorded=_recorded_invocations(connection, trace_id),
        ),
    )
    assembler.add(
        16,
        lambda: disposition_section(
            run_id=run_id, counts=disposition_counts, enumerated=enumerated
        ),
    )
    assembler.add(18, lambda: index_procedure_section(run_id=run_id, chunks_resident=len(resident)))
    assembler.add(21, lambda: encoder_parity_section(run_id=run_id, measurement=_parity()))

    # The three censuses are built from everything above, so they are built last
    # and from whatever was built — a report missing item 12 still publishes an
    # honest census of the figures it does have, which is what makes the gap list
    # the only thing standing between this run and an emitted report.
    assembler.add(
        17,
        lambda: total_checks_section(
            run_id=run_id, checks=collect_total_checks(assembler.sections)
        ),
    )
    assembler.add(19, lambda: reproduction_section(run_id=run_id, sections=assembler.sections))
    assembler.add(20, lambda: scope_labels_section(run_id=run_id, sections=assembler.sections))

    published = tuple(sorted(section.item for section in assembler.sections))
    gaps = tuple(sorted(assembler.gaps, key=lambda gap: gap.item))
    if gaps:
        return PublicationOutcome(
            run_id=run_id,
            items_published=published,
            gaps=gaps,
            aborted_at=aborted_at,
        )

    # **Both artifacts, or neither.** They are rendered before either is opened,
    # so a refusal from `results_manifest` — a duplicate figure label, which is
    # what makes a reproduction unable to say which of two figures moved — cannot
    # leave a report on disk with no manifest beside it. A failure here is
    # reported as a gap against the whole publication for the reason `_Assembler`
    # gives: the run's rows are committed and its record closed, and an escaping
    # exception would turn a finished run into a failed one over a file.
    try:
        rendered = build_report(assembler.sections, run_id=run_id)
        manifest = results_manifest(assembler.sections, run_id=run_id)
    except Exception as error:  # noqa: BLE001 - a refused artifact is a named outcome
        return PublicationOutcome(
            run_id=run_id,
            items_published=published,
            gaps=(
                ItemGap(
                    item=REPORT_CONTENTS[-1].number,
                    title="the assembled report itself",
                    obliged_by=("FR-071", "FR-074"),
                    reason=f"{type(error).__name__}: {error}",
                ),
            ),
            aborted_at=aborted_at,
        )

    report_path = _resolve(REPORT_PATH, root)
    manifest_path = _resolve(RESULTS_MANIFEST_PATH, root)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    # `newline="\n"` on both: the manifest is compared byte for byte by the
    # reproduction gate (FR-074), and a translated line ending would make every
    # line differ on the first run from a checkout with a different `core.autocrlf`.
    report_path.write_text(rendered, encoding="utf-8", newline="\n")
    manifest_path.write_text(manifest, encoding="utf-8", newline="\n")
    return PublicationOutcome(
        run_id=run_id,
        items_published=published,
        gaps=(),
        report_path=report_path,
        manifest_path=manifest_path,
    )


#: The repository root, from this file: `src/model/src/model/ingest/publish.py`
#: → five parents is `src/model`, and two more is the checkout.
_ENTRY_ROOT = Path(__file__).resolve().parents[3]
_REPO_ROOT = _ENTRY_ROOT.parents[1]


def _resolve(relative: Path, root: Path | None) -> Path:
    return (root if root is not None else _REPO_ROOT) / relative


def _require(value: object) -> object:
    """Turn a `None` produced upstream into the refusal its builder would raise.

    Used where a builder's input could not be computed at all — a chunking
    profile over zero chunkings, an attempt ledger over zero attempts. Raising
    here rather than passing `None` on keeps the gap's reason a sentence about
    the missing data instead of a `TypeError` about a `NoneType`.
    """
    if value is None:
        raise ReportError(
            "the data this item is computed from does not exist for this run, so the item "
            "is recorded as a gap rather than published from nothing (FR-068)"
        )
    return value


def _profile(evidence: RunEvidence):  # noqa: ANN202 - `ChunkingProfile | None`
    """The chunking profile, or `None` where the run chunked nothing measurable.

    `profile_chunkings` refuses an empty list, and `LayerChunking.percentiles`
    refuses an empty layer — a run that chunked only one layer has no median
    for the other. Both are gaps rather than exceptions, so they are caught here
    and reported per item.
    """
    if not evidence.chunkings:
        return None
    try:
        profile = profile_chunkings(evidence.chunkings)
        for layer in ("REAL", "SYNTHETIC", "pooled"):
            profile.by_layer[layer].percentiles  # noqa: B018 - the refusal is the point
    except ReportError:
        return None
    return profile


def _attempt_ledger(
    extractions: Sequence[ExtractionStageResult], attempted: int
) -> AttemptLedger | None:
    """FR-069's ledger over the run, or `None` where nothing was attempted.

    The three resolutions are enumerated from what each document's stage
    produced; `attempted` is derived by `cli.count_attempts` from the corpus
    shape. The two derivations are independent, which is the only arrangement
    under which `unaccounted` measures anything.
    """
    if attempted <= 0 or not extractions:
        return None
    return AttemptLedger(
        attempted=attempted,
        stored=sum(result.attempts_stored for result in extractions),
        failed=sum(result.attempts_failed for result in extractions),
        correct_negatives=sum(result.correct_negatives for result in extractions),
    )


def _resident_chunks(connection: object):  # noqa: ANN202 - `tuple[ChunkVector, ...]`
    """The resident generation set's chunks, or an empty tuple if unreadable.

    Read rather than reused from memory, which is what makes items 11 and 18
    recomputable by query (FR-072's corpus-resident scope). An unreadable result
    becomes an empty tuple and therefore two gaps, rather than aborting the run's
    last step.
    """
    try:
        return read_resident_chunks(connection)
    except Exception:  # noqa: BLE001 - any driver failure is a gap, not an abort
        return ()


def _recorded_invocations(connection: object, trace_id: str) -> int:
    from model.ingest.cli import count_recorded_invocations

    return count_recorded_invocations(connection, trace_id)


def _parity():  # noqa: ANN202 - `ParityMeasurement`
    from model.ingest.embed import parity_against_reference

    return parity_against_reference()


class _Lazy:
    """A value computed at most once, on the first item that needs it.

    The reference set costs a whole reproduction of the synthetic layer and two
    items need it; computing it eagerly would charge every run for the item 12
    it may have no data for, and computing it twice would verify the same
    digests twice to answer the same question.
    """

    def __init__(self, build) -> None:  # noqa: ANN001 - a nullary factory
        self._build = build
        self._value: object | None = None

    def get(self) -> object:
        if self._value is None:
            self._value = self._build()
        return self._value


def _build_reference():  # noqa: ANN202 - `ReferenceSet`
    """Reproduce and verify the reference set, or state the refusal (FR-067).

    Deferred import: `reference` reaches `corpus.generate`, which reproduces the
    synthetic layer in memory, and a module-level import would make every
    consumer of this driver pay for the generator.

    **Always the committed corpus.** No root is threaded in from the caller: the
    expected side of every accuracy figure is the corpus the run ingested, and a
    reference reproduced from somewhere else would be a figure about nothing.
    """
    from model.ingest.reference import ReferenceSetError, build_reference_set

    try:
        return build_reference_set()
    except ReferenceSetError as error:
        raise ReportError(f"FR-067: the reference set could not be verified: {error}") from error


def _unattempted_section(*, run_id: str, attempted_fields: Sequence[str], reference: _Lazy):
    """Item 14, from the verified reference set's two printed populations."""
    verified = reference.get()
    return unattempted_fields_section(
        run_id=run_id,
        printed_counts=verified.printed_counts(),
        printed_without_term=verified.printed_without_term(),
        attempted_fields=attempted_fields,
    )


def _quality_section(
    *,
    run_id: str,
    records: Sequence[DocumentRecord],
    extractions: Sequence[ExtractionStageResult],
    reference: _Lazy,
) -> Section:
    """Item 12, scoring both extractors against the verified reference (FR-050).

    The model path's produced values come from the run's own stage results; the
    baseline reads the rendered documents again through its own scan. Both are
    scored by `quality.score_against_reference` against the same reference set,
    over the same documents, so the comparison is of the two extractors and not
    of two scoring conventions.
    """
    from model.ingest.quality import (
        MEASURED_LAYER,
        baseline_values,
        measured_documents,
        score_against_reference,
    )
    from model.ingest.reference import unmeasured_layers

    measured = measured_documents(records)
    if not measured:
        raise ReportError(
            "FR-060: no document on the measured layer was enumerated, so precision and "
            "recall would be computed over nothing"
        )
    produced: dict[str, list[tuple[str, str]]] = {}
    for result in extractions:
        document_id = getattr(result, "document_id", None)
        if document_id is None:
            continue
        produced[document_id] = [
            (value.field_name, value.value_text) for value in getattr(result, "values", ())
        ]
    if not any(produced.values()):
        raise ReportError(
            "FR-060 / FR-068: this run stored no extracted value, so every per-field "
            "precision and recall figure would rest on an empty denominator. The layer is "
            "not published as `0/0` (SC-047); the item is recorded as a gap."
        )

    verified = reference.get()
    model_counts = score_against_reference(verified, produced)
    baseline_counts = score_against_reference(
        verified,
        {
            document_id: [(value.field_name, value.value_text) for value in values]
            for document_id, values in baseline_values(measured).items()
        },
    )
    return extraction_quality_section(
        run_id=run_id,
        model_figures=per_field_figures(model_counts),
        baseline_figures=per_field_figures(baseline_counts),
        unmeasured_layers=unmeasured_layers([MEASURED_LAYER]),
    )
