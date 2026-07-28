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

**The run aborts through this module, and the record it leaves is a run-level
failure** (FR-056, T077). The per-document handler catches *outside*
`with conn.transaction()` — that is `writer.write_generations` — and what is
written afterwards is an `UPDATE` on `ingestion_run`, in a fresh transaction,
never an `extraction_failure` row. `RunLevelFailure` is the five-valued closed
set as a value: it is constructed through one function per kind, each of which
requires the subject FR-056 names for that kind, so a failure recording only
"failed" is unconstructible rather than merely unhelpful.

**Every enumerated document gets exactly one disposition** (FR-073, T086).
`DispositionLedger` partitions the enumerated corpus four ways and **asserts the
sum** at construction. A ledger that does not sum is the defect the requirement
exists to prevent, so it is a refusal at the type rather than a total printed
beside four numbers a reader has to add up.

**The entry point is offline in both its modes** (FR-044, FR-045, T079). `main`
selects `record` or `replay` explicitly — there is no default, because the two
differ by whether the run spends money — and `replay` reaches no network at all:
every response resolves from committed fixtures through the gateway's own store,
and a miss is FR-056's `fixture_missing` rather than a fallback to the provider.
`tests/checks/test_ingest_offline_only.py` (T080) asserts the other half — that
no request-serving entry point can reach this module at all.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Collection, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

from model.corpus.manifest import LAYER_REAL, LAYER_SYNTHETIC
from model.ingest.documents import DocumentRecord
from model.ingest.report import (
    ATTEMPT_UNIT,
    COUNTING_UNITS,
    DISPOSITION_INGESTED,
    DISPOSITION_MEANINGS,
    DISPOSITION_NOT_REACHED,
    DISPOSITION_ROLLED_BACK,
    DISPOSITION_SKIPPED_UNCHANGED,
    DISPOSITIONS,
    DOCUMENT_UNIT,
    INVOCATION_UNIT,
    AttemptLedger,
    Figure,
    FigureScope,
    InvocationLedger,
    Section,
    TotalCheck,
)
from model.ingest.runs import (
    RUN_FAILURE_KINDS,
    RUN_FAILURE_SUBJECTS,
    DocumentPlan,
    record_run_failure,
)
from model.ingest.writer import DocumentOutcome

__all__ = [
    "ATTEMPT_UNIT",
    "COUNTING_UNITS",
    "DISPOSITIONS",
    "DISPOSITION_INGESTED",
    "DISPOSITION_MEANINGS",
    "DISPOSITION_NOT_REACHED",
    "DISPOSITION_ROLLED_BACK",
    "DISPOSITION_SKIPPED_UNCHANGED",
    "DOCUMENT_UNIT",
    "EXCLUDED_DOCUMENT_TYPE",
    "EXCLUSION_REASON",
    "EXTRACTED_DOCUMENT_TYPE",
    "INVOCATION_UNIT",
    "PROVIDER_OPT_IN_ENV_VAR",
    "PROVIDER_OPT_IN_PERMITTED_VALUE",
    "RECORD_MODE",
    "REPLAY_MODE",
    "RESOLUTION_MODES",
    "RESOLUTION_MODE_ENV_VAR",
    "RUN_FAILURE_KINDS",
    "AttemptLedger",
    "DispositionLedger",
    "ExtractionScope",
    "InvocationLedger",
    "InvocationReconciliation",
    "OrchestrationError",
    "RunLevelFailure",
    "RunTrace",
    "abort_run",
    "attempted_invocation_count",
    "build_disposition_ledger",
    "corpus_digest_mismatch",
    "count_attempts",
    "count_recorded_invocations",
    "document_id_collision",
    "documents_by_layer",
    "exclusion_section",
    "fixture_missing",
    "main",
    "oversized_sentence",
    "provider_unreachable",
    "reconcile_invocations",
    "resolve_resolution_mode",
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


# ---------------------------------------------------------------------------
# FR-056 — the run-level failure, constructed with its required subject (T077)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunLevelFailure:
    """One of FR-056's closed five, with the detail that kind is required to say.

    **Not an exception.** The abort has already happened by the time one of
    these exists — `writer.write_generations` caught it outside the
    `with conn.transaction()` block and the rollback has completed — so this is
    the *record* of it, on its way to two columns. Modelling it as a value
    rather than as an exception type is what lets the classification be tested
    without a database and lets the ledger name the document in flight.

    `document_id` is the document in flight, which FR-056 requires wherever one
    exists. It is `None` only for the kinds that arise before any document is
    begun — an identifier collision is corpus-wide (FR-052), and a manifest
    digest can be checked before a document is chosen.
    """

    kind: str
    detail: str
    document_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in RUN_FAILURE_KINDS:
            raise OrchestrationError(
                f"FR-056: {self.kind!r} is outside the closed five {list(RUN_FAILURE_KINDS)}. "
                f"The five run-level kinds and the seven per-field outcomes share zero "
                f"values, so a per-field outcome recorded here would read as the reason the "
                f"whole run stopped."
            )
        if not self.detail.strip():
            raise OrchestrationError(
                f"FR-056: a {self.kind!r} failure records {RUN_FAILURE_SUBJECTS[self.kind]}, "
                f"and this one carries no detail at all."
            )

    @property
    def recorded_detail(self) -> str:
        """The detail as written, with the document in flight named (FR-056)."""
        if self.document_id is None:
            return self.detail
        return f"document in flight {self.document_id}: {self.detail}"


def corpus_digest_mismatch(
    *, document_id: str, path: str, recorded: str, observed: str
) -> RunLevelFailure:
    """FR-005's abort. The subject is the document in flight and the file."""
    return RunLevelFailure(
        kind="corpus_digest_mismatch",
        detail=(
            f"{path} does not match its manifest content_hash; recorded {recorded}, "
            f"found {observed}"
        ),
        document_id=document_id,
    )


def document_id_collision(*, identifier: str, paths: Sequence[str]) -> RunLevelFailure:
    """FR-052's abort. The subject is **both** files and the identifier.

    Both, because one file plus an identifier does not say what it collided
    with, and the identifier alone says nothing about where to look. The corpus
    -wide check runs before the first transaction, so no document is in flight.
    """
    if len(paths) < 2:
        raise OrchestrationError(
            f"FR-056: a document_id_collision records both colliding files; got "
            f"{list(paths)}. One file is not a collision."
        )
    return RunLevelFailure(
        kind="document_id_collision",
        detail=f"{sorted(paths)} both mint the identifier {identifier!r}",
    )


def oversized_sentence(
    *, document_id: str, page_number: int, structural_unit: str
) -> RunLevelFailure:
    """FR-014's abort — the one leaf the ladder cannot descend below."""
    return RunLevelFailure(
        kind="oversized_sentence",
        detail=(
            f"page {page_number}, structural unit {structural_unit!r} holds a single "
            f"sentence over the content budget; the ladder has no class below a sentence"
        ),
        document_id=document_id,
    )


def fixture_missing(*, resolution_key: str, document_id: str | None = None) -> RunLevelFailure:
    """FR-045's abort. The subject is the resolution key that missed.

    The key rather than the prompt, because the key is what a re-record run is
    driven by: E006 inherits E004's resolution key rather than declaring a
    second, so a changed prompt or output-schema constraint resolves to a
    different key and therefore to a miss — which is the signal that the
    fixtures must be re-recorded (`src/model/README.md`).
    """
    return RunLevelFailure(
        kind="fixture_missing",
        detail=(
            f"no committed fixture for resolution key {resolution_key}; the prompt text or "
            f"an output schema constraint has moved and the fixtures must be re-recorded"
        ),
        document_id=document_id,
    )


def provider_unreachable(
    *, provider: str, model: str, document_id: str | None = None
) -> RunLevelFailure:
    """The fifth kind. The subject is the provider and the model addressed."""
    return RunLevelFailure(
        kind="provider_unreachable",
        detail=f"provider {provider!r} addressing model {model!r} could not be reached",
        document_id=document_id,
    )


def abort_run(connection: object, run_id: UUID | str, failure: RunLevelFailure) -> RunLevelFailure:
    """Record the run-level failure, after the rollback, in a fresh transaction.

    Args:
        connection: the run's **autocommit** connection, no longer inside any
            transaction. `runs.record_run_failure` refuses it otherwise, which
            is HINT-002 enforced rather than remembered.
        run_id: the run that aborted.
        failure: what aborted it, from the closed five.

    Returns:
        The failure as recorded, so a caller publishes the same value the row
        holds rather than re-deriving it.

    **This is called after `write_generations` has returned, never from inside
    it.** The generator's handler is already outside the document's
    `with conn.transaction()` block — a nested `transaction()` in psycopg is a
    savepoint, so a handler inside would roll back to the savepoint and let the
    outer block commit a half-written document — and this write happens later
    still, in a transaction of its own, because a row written inside *d*'s
    transaction to explain why *d* failed rolls back with *d*.
    """
    record_run_failure(connection, run_id, kind=failure.kind, detail=failure.recorded_detail)
    return failure


# ---------------------------------------------------------------------------
# FR-073 — the four-way per-document disposition ledger (T086)
# ---------------------------------------------------------------------------

#: FR-073's closed four are `report.DISPOSITIONS`, re-exported here as the three
#: counting units already are. They live in `report` because `cli` imports
#: `report` and the reverse edge would be a cycle; naming them in both places
#: would be two answers about a partition whose whole content is that it is one.


@dataclass(frozen=True)
class DispositionLedger:
    """Every enumerated document under exactly one of FR-073's four (T086).

    **The sum is asserted at construction, not printed at the end.** A ledger
    whose four counts do not add up to the enumerated corpus is the defect the
    requirement exists to prevent — a document silently dropped by a run is
    invisible precisely because nothing claims it — so this refuses to exist
    rather than publishing four numbers a reader has to add up for themselves.

    The four sequences are held rather than four integers because a count with
    no membership behind it cannot be checked: the partition is asserted over
    the *identifiers*, so a document appearing under two dispositions is caught
    where four counts summing correctly would hide it.
    """

    enumerated: tuple[str, ...]
    ingested: tuple[str, ...]
    skipped_unchanged: tuple[str, ...]
    rolled_back: tuple[str, ...]
    not_reached: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.enumerated:
            raise OrchestrationError(
                "FR-073 / FR-068: the disposition ledger enumerated zero documents. An empty "
                "population fails rather than passes — four zeros summing to zero is a "
                "ledger that balances because nothing happened."
            )
        if len(set(self.enumerated)) != len(self.enumerated):
            raise OrchestrationError(
                f"FR-073: the enumerated corpus names a document twice: "
                f"{sorted({d for d in self.enumerated if self.enumerated.count(d) > 1})}"
            )
        assigned: dict[str, list[str]] = {}
        for disposition in DISPOSITIONS:
            for document_id in getattr(self, disposition):
                assigned.setdefault(document_id, []).append(disposition)

        twice = {d: names for d, names in assigned.items() if len(names) > 1}
        if twice:
            raise OrchestrationError(
                f"FR-073: {sorted(twice)} carry more than one disposition "
                f"({twice}). The four partition the corpus; a document under two of them is "
                f"counted twice and the ledger sums by accident."
            )
        outside = sorted(set(assigned) - set(self.enumerated))
        if outside:
            raise OrchestrationError(
                f"FR-073: {outside} carry a disposition but were never enumerated. A "
                f"disposition for a document outside the corpus inflates the sum to match "
                f"and hides whichever enumerated document is missing."
            )
        missing = sorted(set(self.enumerated) - set(assigned))
        if missing:
            raise OrchestrationError(
                f"FR-073: {missing} were enumerated and carry no disposition. Every "
                f"enumerated document carries exactly one of {list(DISPOSITIONS)}, so a "
                f"document with none is a run that lost track of it."
            )
        # The sum, as an assertion rather than a printed total. Redundant given
        # the three checks above and kept anyway: it is the property SC-055
        # states, and a check that restates the requirement in the requirement's
        # own terms is the one a reader can compare against it.
        total = sum(len(getattr(self, disposition)) for disposition in DISPOSITIONS)
        if total != len(self.enumerated):
            raise OrchestrationError(
                f"FR-073: the four dispositions hold {total} documents and the run "
                f"enumerated {len(self.enumerated)}."
            )

    @property
    def counts(self) -> dict[str, int]:
        """The four counts, **including zeros** (FR-073).

        Keyed by all four members in their declared order, so a disposition
        holding no documents is published as a zero rather than omitted — which
        is the difference between "no document was rolled back" and "nobody
        looked".
        """
        return {disposition: len(getattr(self, disposition)) for disposition in DISPOSITIONS}

    @property
    def population(self) -> int:
        return len(self.enumerated)


def build_disposition_ledger(
    *,
    enumerated: Sequence[str],
    plans: Sequence[DocumentPlan],
    outcomes: Sequence[DocumentOutcome],
) -> DispositionLedger:
    """Partition the enumerated corpus from the plan and what the run did.

    Args:
        enumerated: every document the run enumerated, in enumeration order.
            The whole corpus, including the documents extraction does not reach
            — FR-073 partitions the *corpus*, not the extraction scope.
        plans: FR-043's per-document decision. A plan whose tuple was unchanged
            is `skipped_unchanged`; the rest were offered to the writer.
        outcomes: what `writer.write_generations` yielded, in order. One without
            an error is `ingested`; one with an error is `rolled_back`, and the
            generator stops there.

    Returns:
        The ledger, whose construction has already asserted the sum.

    Raises:
        OrchestrationError: the four do not partition the enumerated corpus.

    **`not_reached` is derived by subtraction and that is deliberate.** It is
    the one disposition nothing observes directly — a document never begun
    leaves no trace of having been skipped over — so it is what remains after
    the other three are assigned. Deriving it the other way round, by counting
    what the loop did not get to, would require the loop to know how far it did
    not get.
    """
    skipped = tuple(plan.document_id for plan in plans if plan.unchanged)
    ingested = tuple(outcome.document_id for outcome in outcomes if outcome.committed)
    rolled_back = tuple(outcome.document_id for outcome in outcomes if not outcome.committed)
    accounted = {*skipped, *ingested, *rolled_back}
    not_reached = tuple(document_id for document_id in enumerated if document_id not in accounted)
    return DispositionLedger(
        enumerated=tuple(enumerated),
        ingested=ingested,
        skipped_unchanged=skipped,
        rolled_back=rolled_back,
        not_reached=not_reached,
    )


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


# ---------------------------------------------------------------------------
# FR-044 / FR-045 — the offline console entry, in record and replay (T079)
# ---------------------------------------------------------------------------

#: `ck_ingestion_run__resolution_mode`'s closed pair, and the gateway's own two
#: (TR-021). **No default**: the two differ by whether the run spends real
#: money, so an unset mode is a decision nobody made rather than a decision to
#: do the cheaper thing.
RECORD_MODE: Final[str] = "record"
REPLAY_MODE: Final[str] = "replay"
RESOLUTION_MODES: Final[tuple[str, ...]] = (RECORD_MODE, REPLAY_MODE)

#: The gateway's published controls, as **literals**.
#:
#: `model.ingest` may not import `gateway` — only `model.llm` may (FR-023,
#: AD-001) — so these names cannot be imported from the module that owns them,
#: and a job that has to *select* the mode has to name the variable somehow.
#: The duplication is closed the only way it can be:
#: `src/model/tests/ingest/test_offline_entry.py` imports `gateway.config` and
#: asserts these three literals equal `MODE_ENV_VAR`, `PROVIDER_OPT_IN_ENV_VAR`
#: and `PROVIDER_OPT_IN_PERMITTED_VALUE`. A test may import what a source module
#: may not, so the second copy is checked rather than trusted.
RESOLUTION_MODE_ENV_VAR: Final[str] = "GATEWAY_MODE"
PROVIDER_OPT_IN_ENV_VAR: Final[str] = "GATEWAY_ALLOW_PROVIDER_CALLS"
PROVIDER_OPT_IN_PERMITTED_VALUE: Final[str] = "1"


def resolve_resolution_mode(mode: str, env: MutableMapping[str, str] | None = None) -> str:
    """Select the run's resolution mode, and refuse `record` without its opt-in.

    Args:
        mode: `record` or `replay`, chosen explicitly on the command line.
        env: the environment to configure. Defaults to the process environment;
            taken as a parameter so a test can supply one without mutating
            `os.environ` and acquiring an ordering dependency nothing declares.

    Returns:
        The mode, after writing it into the environment the traced path reads.

    Raises:
        OrchestrationError: the mode is outside the two, or `record` was asked
            for without the provider opt-in set to its one permitted value.

    **Two independent decisions for one network call** (TR-027, TR-063).
    Selecting `record` is a configuration choice; selecting it *and* setting the
    opt-in is a deliberate one. Refusing here rather than letting the gateway
    refuse costs nothing and produces a message about the *run* — the ingestion
    job has already enumerated 51 documents by the time the first invocation
    leaves, and a refusal at that point reads as a failure of the corpus.

    **`replay` reaches no network** (FR-045, SC-023). It resolves every response
    from the committed fixture store, and a miss raises rather than falling back
    to the provider — which is FR-056's `fixture_missing`, a named run-level
    abort, and the whole reason the fixture-miss path is a requirement rather
    than an implementation detail.
    """
    target = os.environ if env is None else env
    if mode not in RESOLUTION_MODES:
        raise OrchestrationError(
            f"FR-045: resolution mode {mode!r} is outside {list(RESOLUTION_MODES)}. There is "
            f"no default: the two differ by whether the run reaches the provider and spends "
            f"real money, so an unstated mode is a decision nobody made."
        )
    opted_in = target.get(PROVIDER_OPT_IN_ENV_VAR) == PROVIDER_OPT_IN_PERMITTED_VALUE
    if mode == RECORD_MODE and not opted_in:
        raise OrchestrationError(
            f"FR-045: `record` mode reaches the provider and is refused unless "
            f"{PROVIDER_OPT_IN_ENV_VAR} is set to exactly "
            f"{PROVIDER_OPT_IN_PERMITTED_VALUE!r}. Selecting the mode and setting the opt-in "
            f"are two independent decisions on purpose — one is a configuration slip, both "
            f"together are a choice. Continuous integration sets neither, and "
            f"`tests/checks/test_ci_provider_gate_absent.py` asserts the absence."
        )
    target[RESOLUTION_MODE_ENV_VAR] = mode
    return mode


def _parser() -> argparse.ArgumentParser:
    """The `ingest` console entry's arguments.

    `--mode` is `required=True` rather than defaulted, which is TR-021's "no
    default" expressed where an operator meets it: a run with no mode stated
    fails at argument parsing, before a manifest is read.
    """
    parser = argparse.ArgumentParser(
        prog="ingest",
        description=(
            "Offline document ingestion (E006). Runs in `record` or `replay`; `replay` "
            "resolves every model response from committed fixtures and reaches no network "
            "(FR-044, FR-045)."
        ),
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=list(RESOLUTION_MODES),
        help="resolution mode; no default, because the two differ by whether the run bills",
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=None,
        help="corpus root to enumerate; defaults to the committed corpus locations",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help=(
            "permit this run to replace resident generations. Requires the schema-owning "
            "role for the whole run (ADR-0020, README section Promotion); an unattended run "
            "under the application role leaves this unset and refuses a resident predecessor"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """The `ingest` console entry (FR-044, FR-045, ADR-0011).

    Args:
        argv: the command line, or `None` for `sys.argv[1:]`.

    Returns:
        A process exit code: 0 when the enumeration resolved, 2 when the run was
        refused. Refusals are exit codes rather than tracebacks because this is
        a job an operator runs from a runbook.

    **What this entry drives today, stated rather than implied.** It selects the
    resolution mode, enumerates the committed corpus, verifies every content
    hash before anything is parsed (FR-005), mints every document identifier
    with the corpus-wide collision refusal that precedes the first transaction
    (FR-052), and prints the enumeration and the extraction partition. The write
    stage — `runs.plan_documents` into `writer.write_generations`, then
    `abort_run` or `finish_run` — is driven from this entry once the extraction
    stage it feeds is assembled. That assembly is **not** a task at T075–T087
    and is left as a stated gap rather than half-wired here: an entry that wrote
    chunks and then reported zero extracted values would publish a corpus that
    looks ingested and is not, which is the recorded-versus-inferred distinction
    FR-022 exists to keep.

    **Offline in both modes, and the claim is structural.** Nothing in
    `model.ingest` imports `gateway`; the traced path is
    `model.llm.extraction`'s alone, it reaches the network only in `record`, and
    only with the opt-in set. In `replay` every response resolves from the
    committed store and a miss raises rather than falling back — asserted from
    the other side by `tests/checks/test_ingest_offline_only.py`, which refuses
    any import path from a request-serving entry point into this package.
    """
    arguments = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    # Imported here rather than at module scope: the corpus reader is needed
    # only by this entry, and a module-level import would make every consumer of
    # the ledger and scope types pay for the manifest machinery.
    from model.ingest.documents import build_documents
    from model.ingest.manifest_reader import ManifestReadError, iter_entries, verify_hash

    try:
        resolve_resolution_mode(arguments.mode)
    except OrchestrationError as error:
        print(f"ingest: {error}", file=sys.stderr)
        return 2

    try:
        documents = tuple(iter_entries(arguments.corpus_root))
        for document in documents:
            verify_hash(document)
        records = build_documents(documents)
        scope = select_extraction_documents(records)
        layers = documents_by_layer(records)
    except (ManifestReadError, OrchestrationError, ValueError) as error:
        # FR-005 and FR-052 both abort **before** the first transaction, so no
        # run record exists yet to carry a run-level failure and nothing has been
        # written to roll back. The kinds those aborts are recorded under once a
        # run record does exist are `corpus_digest_mismatch` and
        # `document_id_collision` — built by the two constructors above, which is
        # why they take the subject FR-056 requires rather than a message.
        print(f"ingest: {error}", file=sys.stderr)
        return 2

    print(
        f"ingest: mode={arguments.mode} promote={arguments.promote} "
        f"documents={len(records)} REAL={layers[LAYER_REAL]} "
        f"SYNTHETIC={layers[LAYER_SYNTHETIC]} "
        f"extraction_attempted={len(scope.attempted)} excluded={len(scope.excluded)}"
    )
    return 0
