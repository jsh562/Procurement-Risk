"""The ingestion job's orchestration: which documents extraction reaches.

FR-022 / FR-070. This module is the offline job's own decision layer — it reads
manifests, chooses, and hands work to the modules that do it. It writes no rows
itself and it never imports `gateway`: every model request leaves from
`model.llm.extraction`, which is the only module permitted to.

**Extraction is restricted to the synthetic layer, and the restriction is a
recorded fact rather than an outcome** (FR-022). The 26 real specifications
yield zero extracted values, and the requirement is explicit that this must not
be left to be inferred from an empty result: an empty table is what a broken
extractor also produces, so "no values for the specifications" and "the
extractor never ran on them" are indistinguishable unless one of them is written
down. `ExtractionScope` is that record — it carries both partitions, the count
of each, and the stated reason — and `exclusion_section` publishes it as item 5
of the report's closed content list.

The reason itself is not an implementation convenience. A UFGS specification
states what a project *requires*; it prints no submitted item, no manufacturer,
and no part number, so there is nothing on it for the transmittal field subset
to find. FR-060 says the same thing from the measurement side: the real layer
has no reference set, so no accuracy figure could be computed for it even if
values were extracted. Both are recorded below, because a reader asking "why is
this corpus half-extracted" should not have to reconstruct the argument.

**One trace identifier per run** (FR-070). `RunTrace` mints it once, before the
first document, and `reconcile_invocations` compares what the run attempted
against what the gateway recorded under it. The identifier is explicit on every
call into `model.llm.extraction` because the gateway reads no ambient context —
which is exactly what makes the reconciliation meaningful: a per-call identifier
would reconcile trivially against itself.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from uuid import uuid4

from model.corpus.manifest import LAYER_REAL, LAYER_SYNTHETIC
from model.ingest.documents import DocumentRecord
from model.ingest.report import (
    ATTEMPT_UNIT,
    COUNTING_UNITS,
    DOCUMENT_UNIT,
    INVOCATION_UNIT,
    AttemptLedger,
    Figure,
    FigureScope,
    InvocationLedger,
    Section,
    TotalCheck,
)

__all__ = [
    "ATTEMPT_UNIT",
    "COUNTING_UNITS",
    "DOCUMENT_UNIT",
    "EXCLUDED_DOCUMENT_TYPE",
    "EXCLUSION_REASON",
    "EXTRACTED_DOCUMENT_TYPE",
    "INVOCATION_UNIT",
    "AttemptLedger",
    "ExtractionScope",
    "InvocationLedger",
    "InvocationReconciliation",
    "OrchestrationError",
    "RunTrace",
    "attempted_invocation_count",
    "count_attempts",
    "count_recorded_invocations",
    "documents_by_layer",
    "exclusion_section",
    "reconcile_invocations",
    "select_extraction_documents",
]


class OrchestrationError(RuntimeError):
    """Raised when the run must not proceed as configured.

    One type, as the rest of this package uses. Each of them means the same
    thing: this run does not continue, and the reason is stated rather than
    worked around.
    """


#: FR-006's two document types, as this decision reads them. Extraction attempts
#: transmittals; specifications are excluded, and both halves are named so the
#: partition is a statement rather than a filter someone can invert by accident.
EXTRACTED_DOCUMENT_TYPE = "transmittal"
EXCLUDED_DOCUMENT_TYPE = "specification"

#: FR-022's recorded reason, in the report's words. Held as a constant so the
#: text the report publishes and the text a failure message quotes are the same
#: string rather than two paraphrases that can drift apart.
EXCLUSION_REASON = (
    "A UFGS specification states what a project requires. It prints no submitted item, "
    "no manufacturer, no part number and no submittal register field, so none of the "
    "declared transmittal field subset (FR-058) can appear on one — extraction is not "
    "attempted rather than attempted and failing. The measurement side agrees: the real "
    "layer has no pre-render reference set (FR-067), so no precision or recall figure "
    "could be computed for it even if values were extracted, and FR-060 publishes that "
    "layer as not measured with this reason."
)


@dataclass(frozen=True)
class ExtractionScope:
    """The corpus partitioned into what extraction reaches and what it does not.

    Both sides are carried, and that is the requirement rather than tidiness: a
    scope holding only the attempted documents would make the exclusion an
    absence, and FR-022 refuses an absence that has to be interpreted.

    `population` names what was partitioned, so the two counts can be checked to
    sum to it — FR-068's rule applied to a partition rather than to a total
    check, which is what stops a document from being silently dropped by a
    classification neither branch claimed.
    """

    attempted: tuple[DocumentRecord, ...]
    excluded: tuple[DocumentRecord, ...]
    reason: str = EXCLUSION_REASON

    @property
    def population(self) -> int:
        return len(self.attempted) + len(self.excluded)

    @property
    def attempted_ids(self) -> tuple[str, ...]:
        return tuple(record.document_id for record in self.attempted)

    @property
    def excluded_ids(self) -> tuple[str, ...]:
        return tuple(record.document_id for record in self.excluded)


def select_extraction_documents(records: Iterable[DocumentRecord]) -> ExtractionScope:
    """Partition the enumerated corpus by whether extraction is attempted (FR-022).

    Args:
        records: every document of the run, both layers. The whole corpus, not
            the synthetic half — the excluded side has to be *enumerated* to be
            recorded, and a caller that filtered before calling this would have
            nothing left to record.

    Returns:
        The partition, with the stated reason attached.

    Raises:
        OrchestrationError: a document is neither a transmittal nor a
            specification, or its type disagrees with its layer. Both are
            refused rather than defaulted into one side: a document silently
            placed on the excluded side is a document nobody extracted and
            nobody noticed, and the mismatch case is how a `document_type`
            defect would first become visible.

    **The classification is on the document type, cross-checked against the
    layer.** Either alone would be enough on today's corpus and neither alone is
    safe: the type is what FR-022 speaks in, and the layer is what carries the
    reference set FR-067 needs. Checking both means a corpus that ever gained a
    synthetic specification or a real transmittal stops the run rather than
    quietly extracting from something with no reference.
    """
    attempted: list[DocumentRecord] = []
    excluded: list[DocumentRecord] = []
    for record in records:
        expected_layer = {
            EXTRACTED_DOCUMENT_TYPE: LAYER_SYNTHETIC,
            EXCLUDED_DOCUMENT_TYPE: LAYER_REAL,
        }.get(record.document_type)
        if expected_layer is None:
            raise OrchestrationError(
                f"FR-022: {record.document_id} is a {record.document_type!r}, which is "
                f"neither {EXTRACTED_DOCUMENT_TYPE!r} nor {EXCLUDED_DOCUMENT_TYPE!r}. "
                f"The partition is closed; a third kind is not silently excluded."
            )
        if record.source_kind != expected_layer:
            raise OrchestrationError(
                f"FR-022: {record.document_id} is a {record.document_type!r} on the "
                f"{record.source_kind} layer, where a {record.document_type!r} is "
                f"{expected_layer}. Extraction is restricted by type and the reference "
                f"set (FR-067) exists only on the synthetic layer, so a document whose "
                f"type and layer disagree would be measured against nothing."
            )
        (attempted if record.document_type == EXTRACTED_DOCUMENT_TYPE else excluded).append(record)
    return ExtractionScope(attempted=tuple(attempted), excluded=tuple(excluded))


def exclusion_section(*, run_id: str, scope: ExtractionScope) -> Section:
    """Report item 5 — the recorded exclusion of the real specifications (FR-022).

    Published as a **census with an enumerated population**: the two counts and
    the enumerated identifiers of the excluded side, so a reader can check that
    the corpus was partitioned rather than that a filter happened to return
    nothing. FR-068's total check carries the population; the figures carry the
    counts.

    Raises:
        ReportError: the partition enumerated nothing. An empty corpus produces
            an empty exclusion and an empty attempt, which is the shape a broken
            manifest read also has — `TotalCheck` refuses it at construction.
    """
    scope_labels = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="census",
        unit="document",
        layer="pooled",
    )
    body = (
        f"Extraction is attempted on the **{len(scope.attempted)} synthetic transmittals** "
        f"and on no other document. The **{len(scope.excluded)} real specifications** are "
        f"excluded, and the exclusion is recorded here rather than left to be inferred "
        f"from an empty result (FR-022): an empty extraction table is also what a broken "
        f"extractor produces, and the two are indistinguishable unless one of them is "
        f"written down.\n\n"
        f"**Reason.** {scope.reason}\n\n"
        f"Excluded documents, enumerated: "
        + ", ".join(f"`{document_id}`" for document_id in scope.excluded_ids)
        + ".\n\n"
        "Consequences that follow, so a reader does not have to derive them: each "
        "excluded document carries zero extracted values, zero contributing chunks, "
        "zero extraction failures, zero line-item associations and zero parse signals, "
        "and is **complete** in that state rather than short (SC-042). It carries "
        "chunks and their run associations like any other document."
    )
    return Section(
        item=5,
        body=body,
        figures=(
            Figure(
                label="Documents extraction is attempted on",
                value=len(scope.attempted),
                scope=FigureScope(
                    run_id=run_id,
                    generation_set="run-scoped",
                    kind="census",
                    unit="document",
                    layer="SYNTHETIC",
                ),
            ),
            Figure(
                label="Documents excluded from extraction",
                value=len(scope.excluded),
                scope=FigureScope(
                    run_id=run_id,
                    generation_set="run-scoped",
                    kind="census",
                    unit="document",
                    layer="REAL",
                ),
                note="Recorded exclusion, not an empty result (FR-022)",
            ),
        ),
        total_checks=(
            TotalCheck(
                name="Every enumerated document is either attempted or excluded",
                population="every document of the run, both layers",
                count=scope.population,
                scope=scope_labels,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# FR-070 — one trace identifier per run, and the reconciliation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunTrace:
    """The run's one trace identifier, and the attempts issued under it.

    **Minted once, before the first document.** FR-070 requires every extraction
    invocation of a run to be issued under one identifier and that identifier to
    be recorded on the ingestion-run record — so it has to exist before the run
    record is written, which is before any document is read.

    The identifier's domain is the gateway's (TR-047): 32 lowercase hexadecimal
    characters, not all zero. It is generated here rather than obtained from
    `gateway.new_trace_id` because this module may not import `gateway` — only
    `model.llm.extraction` may — and `uuid4().hex` is inside the domain by
    construction. The all-zero case is excluded explicitly rather than dismissed
    as improbable, which is the same treatment the gateway's own generator gives
    it.
    """

    trace_id: str

    @classmethod
    def mint(cls) -> RunTrace:
        while True:
            candidate = uuid4().hex
            if candidate != "0" * 32:
                return cls(trace_id=candidate)

    def __post_init__(self) -> None:
        if len(self.trace_id) != 32 or any(
            character not in "0123456789abcdef" for character in self.trace_id
        ):
            raise OrchestrationError(
                f"FR-070: the run trace identifier must be 32 lowercase hexadecimal "
                f"characters (TR-047); got {len(self.trace_id)} characters"
            )
        if self.trace_id == "0" * 32:
            raise OrchestrationError(
                "FR-070: the all-zero trace identifier is reserved as invalid by W3C "
                "Trace Context and would satisfy the run record's NOT NULL while "
                "carrying no information at all"
            )


@dataclass(frozen=True)
class InvocationReconciliation:
    """FR-070's two counts and the verdict, published rather than asserted.

    `attempted` is what the run issued — one per chunk sent to
    `model.llm.extraction`, which is FR-069's invocation unit. `recorded` is what
    `llm_invocation` holds under the run's trace identifier. **Both counts are
    published whether or not they agree** (SC-011): a reconciliation that
    published only the verdict would be a claim about itself.
    """

    trace_id: str
    attempted: int
    recorded: int

    @property
    def agrees(self) -> bool:
        return self.attempted == self.recorded

    @property
    def difference(self) -> int:
        return self.recorded - self.attempted


def reconcile_invocations(
    *, trace_id: str, attempted: int, recorded: int
) -> InvocationReconciliation:
    """Compare invocations attempted against invocations recorded (FR-070).

    Raises:
        OrchestrationError: either count is negative, or `attempted` is zero on a
            run that reached extraction at all. A zero-attempt reconciliation
            agrees trivially with a zero-recorded one, which is the reconciliation
            passing because nothing happened — FR-068's empty-population rule
            reaching the one figure that would otherwise be vacuously true.

    The **inequality is not raised here.** SC-011 requires the two counts to be
    published and required equal; publication is the report's and the equality
    verdict is `agrees`. Raising would prevent the counts from ever being
    published, which is the one outcome the requirement rules out.
    """
    if attempted < 0 or recorded < 0:
        raise OrchestrationError(
            f"FR-070: invocation counts are non-negative; got attempted={attempted}, "
            f"recorded={recorded}"
        )
    if attempted == 0:
        raise OrchestrationError(
            "FR-070: the reconciliation was asked to compare zero attempted invocations, "
            "which agrees with zero recorded ones for no reason at all. A run that "
            "attempted no invocation has no reconciliation to publish; a run that "
            "attempted some and counted none has a defect in its ledger."
        )
    return InvocationReconciliation(trace_id=trace_id, attempted=attempted, recorded=recorded)


#: Counts the rows the gateway wrote under one trace identifier. Parameterized
#: on the identifier alone: the run record's own `run_trace_id` is what joins the
#: two, so a query filtering on anything else would be reconciling against a set
#: the run record cannot name.
_RECORDED_INVOCATIONS = "SELECT count(*) FROM llm_invocation WHERE trace_id = %s"


def count_recorded_invocations(connection: object, trace_id: str) -> int:
    """How many invocation rows carry this run's trace identifier (FR-070).

    Args:
        connection: a psycopg connection. Typed as `object` for the same reason
            `report.read_resident_chunks` is — this module states the query and
            the caller owns the connection, and a narrower annotation would make
            every consumer of this module import psycopg to name the parameter.
        trace_id: the run's identifier.

    Returns:
        The row count, which is the `recorded` side of the reconciliation.

    Raises:
        OrchestrationError: the query returned no row at all, which `count(*)`
            cannot do — so it means the connection is not what it claims to be.
    """
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(_RECORDED_INVOCATIONS, (trace_id,))
        row = cursor.fetchone()
    if row is None:
        raise OrchestrationError(
            f"FR-070: counting invocations recorded under {trace_id} returned no row, "
            f"which `count(*)` cannot do; the connection is not addressing a migrated "
            f"database"
        )
    return int(row[0])


def attempted_invocation_count(
    scope: ExtractionScope, chunks_by_document: Mapping[str, int]
) -> int:
    """One invocation per chunk of every attempted document (FR-069, FR-070).

    The `attempted` side of the reconciliation, derived from the partition and
    the chunk counts rather than from a counter incremented at the call site. A
    counter would be the same number twice — incremented by the loop that issues
    the calls and compared against the rows those calls wrote — and a loop that
    skipped a chunk would decrement its own expectation with it.

    Raises:
        OrchestrationError: an attempted document has no chunk count. A document
            selected for extraction and never chunked is a hole in the ledger,
            and defaulting it to zero would close the hole by ignoring it.
    """
    total = 0
    for record in scope.attempted:
        count = chunks_by_document.get(record.document_id)
        if count is None:
            raise OrchestrationError(
                f"FR-069: {record.document_id} is selected for extraction but has no chunk "
                f"count, so the invocations it should have issued are unknown. An attempt "
                f"ledger with an unknown term does not reconcile."
            )
        total += count
    return total


# ---------------------------------------------------------------------------
# FR-069 — the attempt ledger, and the units it is counted in
# ---------------------------------------------------------------------------


def count_attempts(
    *,
    chunks_by_document: Mapping[str, int],
    attempted_fields: Collection[str],
    absent_fields_by_document: Mapping[str, Collection[str]] | None = None,
) -> int:
    """How many field extractions this run attempted (FR-069).

    Args:
        chunks_by_document: chunk count per document extraction reached. The
            documents extraction did *not* reach contribute nothing and must not
            appear — a specification carries zero attempts, and that is a
            recorded exclusion (FR-022) rather than a zero in this ledger.
        attempted_fields: the declared transmittal subset, unretired at run time
            (FR-024, FR-058). One set for the run, because the subset is
            declared before it and not chosen per document.
        absent_fields_by_document: for each document, the attempted fields it
            printed nowhere. Those collapse from one attempt per chunk to **one
            attempt for the document**, which is the exception FR-069 states.

    Returns:
        The attempt total: for each document, one attempt per present field per
        chunk, plus one attempt for each field the document printed nowhere.

    Raises:
        OrchestrationError: the field subset is empty, a document reports a
            non-positive chunk count, or a document's absent set names a field
            outside the attempted subset. Each is refused rather than defaulted:
            a zero-chunk document has no attempt to count and would silently
            drop out of the denominator, which is how an unaccounted attempt
            hides.

    **Derived, never incremented at the call site.** A counter bumped by the
    loop that issues the work is the same number twice — it would be compared
    against the rows that work produced, and a loop that skipped a chunk would
    decrement its own expectation along with it. This computes the expectation
    from the corpus shape instead, so a skip shows up as a discrepancy.
    """
    fields = set(attempted_fields)
    if not fields:
        raise OrchestrationError(
            "FR-069: the run attempted zero fields, so every attempt count is zero and the "
            "ledger reconciles for no reason at all. An empty subset is a vocabulary or "
            "configuration failure, not a narrow run."
        )
    absences = dict(absent_fields_by_document or {})
    unknown_documents = sorted(set(absences) - set(chunks_by_document))
    if unknown_documents:
        raise OrchestrationError(
            f"FR-069: {unknown_documents} report absent fields but have no chunk count, so "
            f"their attempts cannot be counted. An attempt ledger with an unknown term "
            f"does not reconcile."
        )

    total = 0
    for document_id, chunks in chunks_by_document.items():
        if chunks <= 0:
            raise OrchestrationError(
                f"FR-069: {document_id} is selected for extraction and reports {chunks} "
                f"chunks. A document with no chunk has nothing to attempt a field on, and "
                f"counting it as zero attempts would hide it in the denominator."
            )
        absent = set(absences.get(document_id, ()))
        outside = sorted(absent - fields)
        if outside:
            raise OrchestrationError(
                f"FR-069: {document_id} reports {outside} absent, but they are not in the "
                f"attempted subset. A field nobody attempted is published as "
                f"unattempted-but-printed (FR-058), never as an absence."
            )
        total += (len(fields) - len(absent)) * chunks + len(absent)
    return total


def documents_by_layer(records: Sequence[DocumentRecord]) -> dict[str, int]:
    """Document counts per layer, for the report's per-layer figures.

    Here rather than in `report.py` because the layer is a fact about the
    enumerated corpus, which is this module's to enumerate; the report publishes
    what it is given.
    """
    counts = {LAYER_REAL: 0, LAYER_SYNTHETIC: 0}
    for record in records:
        if record.source_kind not in counts:
            raise OrchestrationError(
                f"{record.document_id}: source_kind {record.source_kind!r} is outside "
                f"{sorted(counts)}"
            )
        counts[record.source_kind] += 1
    return counts
