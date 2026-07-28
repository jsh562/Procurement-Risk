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

**The pipeline runs from the entry, and every stage is another module's**
(FR-044, T097). `run_ingestion` writes the run record with its policy, plans
each document against its recorded input tuple, hands the reloaded ones to
`writer.write_generations`, and feeds each document's transaction the values
`extract.run_extraction_stage` produced for it — through the writer's extraction
hook, before that transaction opens, so `data-model.md` §Write Order steps 0a–7
are untouched and extraction gets no pass of its own over the database. What
this module adds is the ordering and the two closures around it: the run-level
failure and the disposition ledger.

**The run publishes its own account, and that step had no caller either**
(FR-071, T098). After the run record is closed, `publish.publish_report`
assembles all twenty-one items of FR-071's closed content list from this run's
data and writes the report and the results manifest — or, where an item has no
data, returns a **named** refusal saying which items, what obliges each, and why.
It is called after the closure rather than before it so the report describes a
resolved run, and it raises nothing: the documents are committed and durable,
and what is at stake is only whether their account can be published.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Collection, Iterable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

from model.corpus.manifest import LAYER_REAL, LAYER_SYNTHETIC
from model.ingest.chunker import CHUNKER_VERSION, ChunkerError
from model.ingest.documents import DocumentRecord
from model.ingest.embed import embedding_identity
from model.ingest.extract import (
    ExtractionStageError,
    ExtractionStageResult,
    run_extraction_stage,
)
from model.ingest.publish import PublicationOutcome, RunEvidence, publish_report
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
    DECLARED_POLICY,
    RUN_FAILURE_KINDS,
    RUN_FAILURE_SUBJECTS,
    AgentIdentity,
    ConfidencePolicy,
    DocumentPlan,
    RunError,
    RunIdentity,
    finish_run,
    input_tuple_for,
    plan_documents,
    record_run_failure,
    write_run_record,
)
from model.ingest.writer import DocumentOutcome, PreparedDocument, WriterError
from model.llm.extraction import (
    RUN_FAILURE_FIXTURE_MISSING,
    RUN_FAILURE_PROVIDER_UNREACHABLE,
    ExtractionRunFailure,
    Invoker,
)
from model.llm.prompts import prompt_template_digest
from model.llm.schemas import FieldTerm, attempted_terms, output_schema_digest

__all__ = [
    "ATTEMPT_UNIT",
    "COUNTING_UNITS",
    "DISPOSITIONS",
    "EXIT_ABORTED",
    "EXIT_OK",
    "EXIT_REFUSED",
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
    "PRICE_TABLE_PIN_ENV_VAR",
    "PROVIDER_CLIENT",
    "PROVIDER_MODEL",
    "PROVIDER_OPT_IN_ENV_VAR",
    "PROVIDER_OPT_IN_PERMITTED_VALUE",
    "RECORD_MODE",
    "REPLAY_MODE",
    "RESOLUTION_MODES",
    "RESOLUTION_MODE_ENV_VAR",
    "RUN_FAILURE_KINDS",
    "VCS_REVISION_ENV_VAR",
    "AttemptLedger",
    "DispositionLedger",
    "ExtractionScope",
    "InvocationLedger",
    "InvocationReconciliation",
    "OrchestrationError",
    "PublicationOutcome",
    "RunEvidence",
    "RunLevelFailure",
    "RunOutcome",
    "RunTrace",
    "abort_run",
    "attempted_invocation_count",
    "build_disposition_ledger",
    "build_revision",
    "build_run_identity",
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
    "publish_report",
    "reconcile_invocations",
    "require_price_table_pin",
    "resolve_resolution_mode",
    "run_ingestion",
    "select_extraction_documents",
    "unretired_field_names",
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

    **This is the `attempted` side of `report.AttemptLedger` and only that
    side.** The three resolutions — stored, failed, correct negative (FR-069 as
    amended 2026-07-28) — are enumerated by `extract.run_extraction_stage` from
    what it actually produced. Keeping the two derivations apart is what makes
    the ledger's published `unaccounted` a comparison rather than a restatement:
    a total computed by summing the resolutions would balance by construction
    and would publish a zero that measured nothing.
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


def fixture_missing(
    *, resolution_key: str, document_id: str | None = None, cause: str | None = None
) -> RunLevelFailure:
    """FR-045's abort. The subject is the resolution key that missed.

    The key rather than the prompt, because the key is what a re-record run is
    driven by: E006 inherits E004's resolution key rather than declaring a
    second, so a changed prompt or output-schema constraint resolves to a
    different key and therefore to a miss — which is the signal that the
    fixtures must be re-recorded (`src/model/README.md`).

    `cause` is the gateway's own account of the miss, appended rather than
    substituted. It names the store root the lookup ran against, which the key
    alone does not, and dropping it to route through this constructor would
    trade one fact for another.
    """
    return RunLevelFailure(
        kind="fixture_missing",
        detail=(
            f"no committed fixture for resolution key {resolution_key}; the prompt text or "
            f"an output schema constraint has moved and the fixtures must be re-recorded"
            + (f" — {cause}" if cause else "")
        ),
        document_id=document_id,
    )


def provider_unreachable(
    *, provider: str, model: str, document_id: str | None = None, cause: str | None = None
) -> RunLevelFailure:
    """The fifth kind. The subject is the provider and the model addressed.

    `cause` carries the gateway's own diagnosis — the exception type and its
    message — for the reason `fixture_missing`'s does: the provider and the
    model say *what* was addressed, and only the cause says what happened when
    it was.
    """
    return RunLevelFailure(
        kind="provider_unreachable",
        detail=(
            f"provider {provider!r} addressing model {model!r} could not be reached"
            + (f" — {cause}" if cause else "")
        ),
        document_id=document_id,
    )


def _run_level_failure(error: ExtractionRunFailure, document_id: str) -> RunLevelFailure:
    """Route a gateway run-level abort through the constructor for its kind.

    Args:
        error: what `model.llm.extraction` raised. Its `kind` is one of the two
            FR-056 members an invocation can produce, and its `subject` is the
            fact that kind is obliged to name — the resolution key for a fixture
            miss, and `None` for an unreachable provider, whose subject is this
            job's own provider and model rather than anything the gateway
            reported.
        document_id: the document in flight.

    Returns:
        The failure, built by `fixture_missing` or `provider_unreachable`.

    Raises:
        OrchestrationError: the gateway reported a kind outside the two an
            invocation can produce. Refused rather than passed through:
            `RunLevelFailure` would accept `corpus_digest_mismatch` from here
            without complaint, because its own check is only that the value is
            one of the five — and a digest mismatch reported by an invocation is
            a defect in the mapping, not a corpus that changed mid-run.

    **Why this exists at all.** The abort was previously classified by copying
    the gateway's `kind` and `detail` straight into a bare `RunLevelFailure`.
    That produced the right column value on today's code and made FR-056's
    mapping a coincidence rather than a wiring: the two constructors that
    *require* each kind's subject had no caller on the path that actually
    aborts, so nothing held `fixture_missing` to naming a resolution key.
    """
    if error.kind == RUN_FAILURE_FIXTURE_MISSING:
        return fixture_missing(
            resolution_key=error.subject or "unreported by the gateway",
            document_id=document_id,
            cause=error.detail,
        )
    if error.kind == RUN_FAILURE_PROVIDER_UNREACHABLE:
        return provider_unreachable(
            provider=PROVIDER_CLIENT,
            model=PROVIDER_MODEL,
            document_id=document_id,
            cause=error.detail,
        )
    raise OrchestrationError(
        f"FR-056: an extraction invocation reported run-level kind {error.kind!r}, which is "
        f"outside the two an invocation can produce "
        f"({RUN_FAILURE_FIXTURE_MISSING!r}, {RUN_FAILURE_PROVIDER_UNREACHABLE!r}). The other "
        f"three of the closed five arise on the intake path, before any invocation, and each "
        f"has a constructor requiring a subject an invocation does not hold."
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

#: The gateway's price-table pin (TR-048), restated for the same reason and
#: checked by the same test as the three above.
#:
#: **Required in `replay` as well as in `record`**, which is not obvious and is
#: why it is refused here rather than left to be discovered. A replayed
#: invocation is still priced — from the fixture's recorded token counts against
#: the pinned table — so `gateway.orchestrator.require_resolvable_price_pin`
#: refuses *before* the request is built, on every invocation of either mode.
PRICE_TABLE_PIN_ENV_VAR: Final[str] = "GATEWAY_PRICE_TABLE_VERSION"

#: The provider model this job addresses, recorded on `ingestion_run.
#: provider_model` and named on every invocation. A **literal** for the same
#: reason the three controls above are: `gateway.provider.DEFAULT_MODEL` owns it
#: and `model.ingest` may not import `gateway`. Checked rather than trusted by
#: `src/model/tests/ingest/test_offline_entry.py`, which may import what this
#: module may not.
#:
#: Named on the call rather than left to the gateway's default, because the run
#: record has to say which model produced its citations and a run recording one
#: name while addressing another is exactly the unattributable figure Principle I
#: excludes.
PROVIDER_MODEL: Final[str] = "claude-opus-5"

#: What this job addresses the model *through*, and the `provider` half of
#: FR-056's `provider_unreachable` subject — the model alone says what was
#: addressed but not by what route.
#:
#: **The provider distribution's own name is deliberately not written here.**
#: `tests/checks/test_single_import_site.py` permits exactly one source file in
#: the repository to name it, and that file is `gateway/provider.py`, which owns
#: the client; a second copy in this module is precisely the drift the
#: single-import-site rule exists to prevent, and the check refuses it. There is
#: also no public name to import — `gateway.provider` holds it privately, and
#: `model.ingest` may not import `gateway` at all.
#:
#: So the subject is the one this job can state truthfully from its own
#: knowledge: every invocation of this run leaves through `model.llm.extraction`
#: into the shared gateway client, and "the gateway could not reach the model"
#: is what a run that fails here has actually observed. The provider's own
#: diagnosis travels beside it as the failure's `cause`.
PROVIDER_CLIENT: Final[str] = "gateway"

#: FR-038's build revision, when the job runs outside a checkout. Read first, so
#: a container or a runner without `git` on PATH can state what built it; the
#: fallback is `git rev-parse HEAD`, and a run that can determine neither is
#: **refused** rather than recorded under a placeholder. `AgentIdentity` already
#: refuses a non-hexadecimal revision, and inventing one here to get past it
#: would record a revision nobody typed.
VCS_REVISION_ENV_VAR: Final[str] = "INGEST_VCS_REVISION"

#: The three exit codes `main` returns, as a runbook reads them.
#:
#: **0 — the run resolved.** Every enumerated document carries a terminal
#: disposition, the run record carries a finish, and `read_run_state` reads it as
#: `complete`.
#:
#: **2 — the run was refused, before any row was written.** A mode outside the
#: two, `record` without its opt-in, a manifest that does not read, a content
#: hash that does not match, an identifier collision, an unreachable database, or
#: a resident generation this run is not permitted to replace. Nothing was
#: written, so there is nothing to inspect and nothing to undo.
#:
#: **3 — the run aborted part-way, and the corpus is partial.** Documents before
#: the abort are committed and their generations active (FR-042); the document in
#: flight is `rolled_back` and the rest are `not_reached` (FR-073). Distinct from
#: 2 because the two need different operator responses — a refusal is retried
#: after a fix, a partial run is *resumed*, and its skip rule (FR-043) means the
#: retry writes only what the first run did not. Distinct from 1, which this
#: entry never returns deliberately: 1 is what an unhandled traceback exits with,
#: and a runbook must be able to tell a classified abort from a crash.
EXIT_OK: Final[int] = 0
EXIT_REFUSED: Final[int] = 2
EXIT_ABORTED: Final[int] = 3


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


def require_price_table_pin(env: Mapping[str, str] | None = None) -> str:
    """Refuse a run whose invocations could not be priced (TR-048).

    Args:
        env: the environment to read. Defaults to the process environment.

    Returns:
        The configured pin, unvalidated — whether it *resolves* to a seeded
        version is the gateway's question and is asked against the gateway's own
        connection, which this module does not hold.

    Raises:
        OrchestrationError: the pin is unset or blank.

    **Refused here, before the corpus is enumerated, and the position is the
    whole value of this check.** The gateway raises the same refusal, but it
    raises it on the *first invocation* — which this job reaches only after
    chunking, embedding and committing every document extraction does not reach.
    Measured on this corpus that is 26 documents and 6,391 chunks written before
    a missing environment variable is reported, and reported as
    `provider_unreachable`, which is the kind for a provider that could not be
    reached rather than for a configuration nobody set. Both costs are avoided
    by asking the question early; neither is avoided by asking it better later.

    **Not defaulted, for the reason the gateway does not default it either.**
    Every recorded cost cites the price-table version it was computed against, so
    a pin chosen by this module would attribute a run's costs to a table nobody
    selected.
    """
    source = os.environ if env is None else env
    pin = (source.get(PRICE_TABLE_PIN_ENV_VAR) or "").strip()
    if not pin:
        raise OrchestrationError(
            f"TR-048: {PRICE_TABLE_PIN_ENV_VAR} is unset, so no invocation of this run could "
            f"be priced and every extraction would abort. It is required in `replay` too — a "
            f"replayed invocation is priced from the fixture's recorded token counts against "
            f"the pinned table. Set it to a seeded `price_table_version.version_id`; E003's "
            f"revision 0103 seeds `2026-07-26-published`."
        )
    return pin


# ---------------------------------------------------------------------------
# FR-038 — what this run ran with, assembled once (T097)
# ---------------------------------------------------------------------------

#: The terms this run may attempt: every seeded vocabulary name not retired at
#: run time (FR-024). Read from the database rather than from the declaration,
#: because retirement is a run-time fact and the declaration is a committed one —
#: `schemas.attempted_terms` filters the second by the first.
_UNRETIRED_TERMS = "SELECT field_name FROM field_vocabulary WHERE retired_at IS NULL"


def unretired_field_names(connection: object) -> tuple[str, ...]:
    """Every vocabulary term unretired at run time (FR-024).

    Args:
        connection: a psycopg connection. Typed as `object` for the reason
            `count_recorded_invocations` is.

    Returns:
        The names, sorted, so two runs against one database offer the same set
        in the same order and the prompt digest they derive cannot depend on a
        query plan.

    Raises:
        OrchestrationError: the vocabulary is empty. `attempted_terms` refuses
            that too, and refusing here as well is not redundant: this message
            says the *table* is empty, which is an unmigrated or unseeded
            database, while that one says the run offered nothing.
    """
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(_UNRETIRED_TERMS)
        rows = cursor.fetchall()
    names = tuple(sorted(str(row[0]) for row in rows))
    if not names:
        raise OrchestrationError(
            "FR-024: `field_vocabulary` holds no unretired term, so every field would be "
            "unattempted and every document would report no values found. E003's revision "
            "0005 seeds 22 terms; a database with none is not migrated."
        )
    return names


def build_revision(env: Mapping[str, str] | None = None) -> str:
    """The commit this build was made from (FR-038's `vcs_revision`).

    Args:
        env: the environment to read `INGEST_VCS_REVISION` from. Defaults to the
            process environment.

    Returns:
        A lower-case hexadecimal revision, abbreviated or full.

    Raises:
        OrchestrationError: neither the variable nor `git` could supply one.
            **Refused rather than defaulted.** `gateway._gateway_revision` falls
            back to the marker `unknown` and is right to — a fixture with honest
            provenance beats no fixture — but this value goes on
            `ingestion_run.agent_id`, which is the project's only record of what
            produced a citation, and `ck_ingestion_run__agent_id_format` would
            refuse the marker anyway. A run that cannot say what built it is a
            run whose figures cannot be attributed.
    """
    source = os.environ if env is None else env
    declared = source.get(VCS_REVISION_ENV_VAR, "").strip()
    if declared:
        return declared
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - resolved from PATH
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise OrchestrationError(
            f"FR-038: this run's build revision could not be read from `git` ({error}) and "
            f"{VCS_REVISION_ENV_VAR} is unset. Set it to the commit this build was made "
            f"from; it is recorded on every run and is what attributes a stored citation to "
            f"the code that produced it."
        ) from error
    revision = result.stdout.strip()
    if result.returncode != 0 or not revision:
        raise OrchestrationError(
            f"FR-038: `git rev-parse HEAD` reported no revision "
            f"({result.stderr.strip() or 'no output'}) and {VCS_REVISION_ENV_VAR} is unset. "
            f"A run outside a checkout states its revision through that variable rather "
            f"than recording a placeholder."
        )
    return revision


def build_run_identity(
    *,
    mode: str,
    trace_id: str,
    fields: Sequence[FieldTerm],
    manifest_digests: Sequence[str],
    env: Mapping[str, str] | None = None,
) -> RunIdentity:
    """The ten `ingestion_run` columns saying what this run ran with (FR-038).

    Args:
        mode: the resolution mode, already resolved and written into the
            environment.
        trace_id: the run's one trace identifier (FR-070), minted before the run
            record exists because the record carries it.
        fields: the attempted subset, after the run-time retirement filter. The
            prompt digest is taken over *these*, not over the declaration: a
            retired term narrows every resolved prompt, and a digest that
            ignored the narrowing would leave FR-043's input tuple still while
            every request moved.
        manifest_digests: each corpus manifest's own file digest, in location
            order.
        env: the environment `build_revision` reads. Defaults to the process
            environment.

    Returns:
        The identity, ready for `write_run_record`.

    Raises:
        OrchestrationError: the build revision could not be determined.
        RunError: a member is outside the grammar its column admits.

    Every member is derived from something committed — the chunker's version
    string, the verified encoder's identity, the manifests' own digests, the
    prompt template and the output schema — rather than passed in by a caller.
    A run's account of what it ran with is only worth reading if nothing at the
    call site could have written it.
    """
    model_id, model_revision = embedding_identity()
    return RunIdentity(
        agent_id=AgentIdentity(
            principal_kind="automation",
            principal_id="ingest",
            distribution="model",
            version=version("model"),
            vcs_revision=build_revision(env),
        ),
        provider_model=PROVIDER_MODEL,
        chunker_version=CHUNKER_VERSION,
        embedding_model_id=model_id,
        embedding_model_revision=model_revision,
        corpus_manifest_digests=tuple(manifest_digests),
        extraction_prompt_digest=prompt_template_digest(fields),
        extraction_schema_digest=output_schema_digest(),
        resolution_mode=mode,
        run_trace_id=trace_id,
    )


# ---------------------------------------------------------------------------
# FR-044 / FR-073 — the pipeline, driven from the console entry (T097)
# ---------------------------------------------------------------------------


@dataclass
class _ExtractionStep:
    """`run_extraction_stage`, adapted to `writer.ExtractionHook`.

    **Mutable, and holding two things the run needs afterwards.** `failure` is
    the run-level abort classified where FR-056's closed five is already
    written down, and `results` is what each document's extraction produced —
    the invocation counts and absent-field sets FR-069's ledger is denominated
    on. Neither can be a return value: the hook's return is the writer's, and
    the generator that calls it stops at the first failure.

    **Extraction is attempted on the declared partition and on nothing else**
    (FR-022). A document outside `attempted` returns `None`, which the writer
    reads as "extraction did not reach this document" — distinct from a result
    with three empty sequences, which would mean it ran and found nothing.
    """

    fields: tuple[FieldTerm, ...]
    run_id: str
    trace_id: str
    policy: ConfidencePolicy
    attempted: frozenset[str]
    invoke: Invoker | None = None
    results: list[ExtractionStageResult] = field(default_factory=list)
    failure: RunLevelFailure | None = None

    def __call__(self, prepared: PreparedDocument) -> ExtractionStageResult | None:
        document_id = prepared.record.document_id
        if document_id not in self.attempted:
            return None
        try:
            result = run_extraction_stage(
                document_id=document_id,
                chunks=prepared.chunking.chunks,
                fields=self.fields,
                run_id=self.run_id,
                trace_id=self.trace_id,
                policy=self.policy,
                model=PROVIDER_MODEL,
                invoke=self.invoke,
            )
        except ExtractionRunFailure as error:
            # FR-056, classified here because this is where the closed five
            # live — and classified **through the constructor for the kind**
            # rather than by copying the gateway's `kind` and `detail` into a
            # bare `RunLevelFailure`. The bare construction type-checked and
            # produced the right column value, so the mapping held by
            # coincidence: nothing made `fixture_missing` the function that
            # requires the resolution key, and a gateway that ever reported a
            # sixth kind would have written it straight through
            # `RunLevelFailure`'s domain check with no subject at all.
            self.failure = _run_level_failure(error, document_id)
            raise WriterError(
                f"FR-056: extraction of {document_id} aborted the run with "
                f"{error.kind!r}; no generation is written for it and the run-level failure "
                f"is recorded after the rollback"
            ) from error
        except ExtractionStageError as error:
            # The stage refused the document as described — no chunk, no
            # attempted field, or chunks of another document. Not one of
            # FR-056's five and not a per-field outcome either, so it carries no
            # `failure`: it aborts the run with its cause reported and the run
            # record left unclosed. Converted rather than allowed to escape,
            # because an escaping `RuntimeError` would leave this entry exiting
            # 1 with a traceback, which is the one code a runbook cannot read.
            raise WriterError(f"{type(error).__name__}: {error}") from error
        self.results.append(result)
        return result


@dataclass(frozen=True)
class RunOutcome:
    """What one ingestion run did, in the terms the run record and FR-073 use.

    `failure` is present when the abort was one of FR-056's five and was
    therefore recorded on `ingestion_run`; `detail` is present when the run
    aborted for a reason **outside** that closed set, which is recorded nowhere
    and is why the two fields are separate rather than one nullable string.
    """

    run_id: UUID
    ledger: DispositionLedger
    failure: RunLevelFailure | None = None
    detail: str | None = None
    values_written: int = 0
    failures_written: int = 0
    chunks_written: int = 0
    extractions: tuple[ExtractionStageResult, ...] = ()
    #: What the report driver did (FR-071). Present on every outcome the
    #: pipeline produces, emitted or refused, because "no report was written"
    #: and "nobody tried to write one" are the two states this epic spent three
    #: components failing to tell apart. `None` only where the run never reached
    #: the publish step at all.
    publication: PublicationOutcome | None = None

    @property
    def complete(self) -> bool:
        """Whether every enumerated document reached a terminal disposition."""
        return self.failure is None and self.detail is None

    @property
    def invocations(self) -> int:
        """Invocations this run issued (FR-069's unit), summed per document.

        Taken from each stage's own counts rather than from a counter at the
        call site, for the reason `attempted_invocation_count` gives: a loop
        that skipped a chunk would decrement its own expectation along with it.
        """
        return sum(result.invocations for result in self.extractions)

    @property
    def exit_code(self) -> int:
        return EXIT_OK if self.complete else EXIT_ABORTED


def run_ingestion(
    connection: object,
    *,
    records: Sequence[DocumentRecord],
    scope: ExtractionScope,
    mode: str,
    trace_id: str,
    manifest_digests: Sequence[str],
    policy: ConfidencePolicy = DECLARED_POLICY,
    promote: bool = False,
    invoke: Invoker | None = None,
    report_root: Path | None = None,
) -> RunOutcome:
    """Write the run record, then every document, then the finish or the abort.

    Args:
        connection: the run's one **autocommit** connection.
        records: every enumerated document, in enumeration order. The whole
            corpus, both layers — FR-073 partitions what was *enumerated*.
        scope: FR-022's partition. Only its attempted side reaches extraction.
        mode: the resolution mode this run was started in.
        trace_id: the run's one trace identifier (FR-070), minted before the run
            record because the record carries it.
        manifest_digests: each corpus manifest's own file digest.
        policy: the floor and the three deduction weights. Defaulted to the
            project's declaration for the reason `write_run_record` states.
        promote: whether this run may replace resident generations.
        invoke: the gateway entry point, for tests. `None` is the traced path.
        report_root: where the report and the results manifest are written
            (FR-071, FR-074). `None` is the checkout, which is the ordinary
            path; a test passes a temporary directory, so a test run cannot
            replace the committed artifacts with figures from a two-document
            corpus.

    Returns:
        The run's outcome: the disposition ledger, and the failure where one was
        recorded.

    Raises:
        OrchestrationError: a document's tuple moved while a generation of it is
            resident and `promote` is unset. Refused **before** the run record
            is written rather than at the document that would have needed it: an
            unattended run has no privilege to remove a predecessor, so the
            alternative is a run that commits every fresh document and then
            aborts on the first resident one.
        RunError: the run record could not be written or the finish recorded.

    **The order is fixed and each step is another module's** — `plan_documents`
    decides skip or reload (FR-043), `write_generations` writes each reloaded
    document in its own transaction and stops at the first failure (FR-042),
    `run_extraction_stage` feeds that transaction its values through the hook,
    and `abort_run` or `finish_run` closes the record. Nothing here reorders
    `data-model.md` §Write Order steps 0a–7; extraction produces the value rows
    the write consumes and gets no pass of its own over the database.

    **Documents are prepared lazily, one ahead of the write.** Chunking and
    embedding 51 documents is the expensive half of this job, and a run that
    aborts on the first transmittal must not pay for the twenty-four behind it.
    The generator is what makes `not_reached` cost nothing.

    **A document whose preparation fails is `rolled_back`, not `not_reached`.**
    It was the document in flight when the run aborted, which is what FR-073's
    `rolled_back` means; the outcome is synthesized here because the writer
    never saw it.

    **One preparation failure is classified and the rest are not.** FR-014's
    oversized sentence is one of FR-056's closed five, and `ChunkerError` now
    carries the document, the page and the structural unit as attributes — so it
    is routed through `oversized_sentence` and recorded on `ingestion_run` under
    its kind. Every other preparation failure — a parse failure, a containment
    miss — has **no member** among the five, and that remains a stated gap
    rather than an oversight: inventing a kind to fill the column would put a
    value outside the closed set into the column whose whole content is that the
    set is closed. Such a run reads as `in_flight` — one of `runs.RUN_STATES`'
    three, disclosed there for exactly this — and exits 3 with the cause on
    stderr.
    """
    import psycopg

    from model.ingest.writer import prepare_document, write_generations

    # The vocabulary is read before the identity is assembled and not after: the
    # prompt digest is taken over the terms this run will actually attempt, so a
    # retired term moves the digest, moves every document's input tuple (FR-043)
    # and reloads the corpus — which is the whole point of putting it in the
    # tuple. An identity built from the declaration would sit still while every
    # resolved prompt moved.
    fields = attempted_terms(unretired_field_names(connection))
    identity = build_run_identity(
        mode=mode, trace_id=trace_id, fields=fields, manifest_digests=manifest_digests
    )
    tuples = {record.document_id: input_tuple_for(record, identity) for record in records}
    by_id = {record.document_id: record for record in records}

    # Planned before the run record is written, so the refusal below leaves no
    # `ingestion_run` row at all. The plan needs the connection and nothing this
    # run has written, so the ordering costs nothing to have.
    plans = plan_documents(connection, tuples)
    resident = [plan.document_id for plan in plans if plan.promotes]
    if resident and not promote:
        raise OrchestrationError(
            f"FR-055 / {{SAD:ADR-0020}}: {len(resident)} documents have a resident generation "
            f"whose input tuple has moved, and this run may not replace one — first is "
            f"{resident[0]}. Replacing a generation removes its rows under the schema-owning "
            f"role (`--promote`, README section Promotion); the unattended run refuses rather "
            f"than committing the fresh documents and aborting on the first resident one."
        )

    run_id = uuid4()
    write_run_record(connection, run_id=run_id, identity=identity, policy=policy)
    step = _ExtractionStep(
        fields=tuple(fields),
        run_id=str(run_id),
        trace_id=identity.run_trace_id,
        policy=policy,
        attempted=frozenset(scope.attempted_ids),
        invoke=invoke,
    )
    pending = [plan for plan in plans if plan.reloads]
    in_flight: list[str] = []
    # The report's chunking figures (items 8 and 9) range over what this run
    # cut, and the generator that cuts it is consumed by the writer — so the
    # chunkings are recorded as they pass rather than re-derived afterwards,
    # which would chunk the corpus a second time to describe the first.
    evidence = RunEvidence()

    def prepared_documents() -> Iterator[tuple[PreparedDocument, str]]:
        for plan in pending:
            in_flight.append(plan.document_id)
            prepared = prepare_document(by_id[plan.document_id])
            in_flight.pop()
            evidence.record(prepared.record, prepared.chunking)
            yield prepared, plan.digest

    outcomes: list[DocumentOutcome] = []
    unclassified: str | None = None
    preparation_failure: RunLevelFailure | None = None
    try:
        # An explicit loop rather than `list.extend`, so the outcomes yielded
        # before a preparation failure are kept by a statement rather than by
        # `extend`'s incremental-append behaviour. The documents behind those
        # outcomes are committed and durable; a ledger that lost them would
        # report them `not_reached`.
        for outcome in write_generations(
            connection,  # type: ignore[arg-type]
            run_id=run_id,
            documents=prepared_documents(),
            promote=promote,
            extract=step,
        ):
            outcomes.append(outcome)
    except (WriterError, ValueError) as error:
        # Preparation, which happens in the generator and therefore outside the
        # writer's own handler. The document is named as the one in flight.
        #
        # **One of these is classified and the rest are not** (FR-056). FR-014's
        # oversized sentence is one of the closed five, and the chunker now
        # carries the document, page and structural unit `oversized_sentence`
        # requires as attributes rather than only inside its message — so the
        # abort is recorded on `ingestion_run` under its kind instead of leaving
        # the run reading `in_flight` forever with no `run_failure_kind` at all.
        # A parse failure or a containment miss still has no member among the
        # five and is still reported as `unclassified`, which is the stated gap
        # this branch's docstring describes.
        unclassified = f"{type(error).__name__}: {error}"
        if isinstance(error, ChunkerError) and error.is_oversized_sentence:
            preparation_failure = oversized_sentence(
                document_id=str(error.document_id),
                page_number=int(error.page_number or 0),
                structural_unit=str(error.structural_unit),
            )
        if in_flight:
            outcomes.append(
                DocumentOutcome(
                    document_id=in_flight[-1],
                    chunks_written=0,
                    containment=None,
                    error=unclassified,
                )
            )

    ledger = build_disposition_ledger(
        enumerated=[record.document_id for record in records],
        plans=plans,
        outcomes=outcomes,
    )
    detail = unclassified or next(
        (outcome.error for outcome in outcomes if not outcome.committed), None
    )
    # Extraction's failure wins where both exist, which they cannot: the writer
    # stops at the first failure, so a run reaches at most one abort.
    failure = step.failure or preparation_failure
    try:
        if failure is not None:
            # After `write_generations` has returned, on the autocommit
            # connection, in a transaction of its own (HINT-002, FR-056).
            # `record_run_failure` refuses anything else.
            abort_run(connection, run_id, failure)
        elif detail is None:
            finish_run(connection, run_id)
    except (RunError, psycopg.Error) as error:
        # The closure is the last statement of the run and the one whose failure
        # is least visible: the documents are committed and durable either way,
        # and what is lost is the run's own account of itself — it reads as
        # `in_flight` forever. Reported as a detail rather than raised so the
        # caller returns the code for a run that did not resolve rather than the
        # one for a run that was refused before writing anything.
        detail = f"the run's own record was not closed: {type(error).__name__}: {error}"

    # FR-071, and the twenty-two requirements whose publish obligation runs
    # through it. **After the run record is closed**, so the report describes a
    # resolved run rather than one still in flight, and so a failure here cannot
    # cost the run its own account of itself. `publish_report` raises nothing: a
    # section it cannot build becomes a named gap, and a run whose gaps are
    # non-empty emits no report and says which items and why.
    publication = publish_report(
        connection,
        run_id=str(run_id),
        trace_id=identity.run_trace_id,
        records=records,
        layers=documents_by_layer(records),
        exclusion=exclusion_section(run_id=str(run_id), scope=scope),
        disposition_counts=ledger.counts,
        enumerated=ledger.population,
        evidence=evidence,
        extractions=tuple(step.results),
        attempted_fields=[term.name for term in fields],
        policy=policy,
        attempted_invocations=_attempted_invocations(scope, evidence),
        attempted_extractions=_attempted_extractions(scope, evidence, fields, step.results),
        aborted_at=_aborted_at(failure, detail),
        root=report_root,
    )

    return RunOutcome(
        run_id=run_id,
        ledger=ledger,
        failure=failure,
        detail=detail,
        values_written=sum(outcome.values_written for outcome in outcomes),
        failures_written=sum(outcome.failures_written for outcome in outcomes),
        chunks_written=sum(outcome.chunks_written for outcome in outcomes),
        extractions=tuple(step.results),
        publication=publication,
    )


def _chunks_by_document(evidence: RunEvidence) -> dict[str, int]:
    """Chunk count per document this run chunked, for the two ledgers.

    Taken from the chunkings the run actually cut rather than from the corpus,
    because a document the run never reached issued no invocation and attempted
    no field — counting it would put an expectation in the ledger against work
    that was never begun, and the ledger would report a defect on every partial
    run.
    """
    return {chunking.document_id: len(chunking.chunks) for _, chunking in evidence.chunkings}


def _attempted_invocations(scope: ExtractionScope, evidence: RunEvidence) -> int:
    """FR-070's `attempted` side, over the documents extraction reached.

    `attempted_invocation_count` refuses an attempted document with no chunk
    count, which is right for a complete run and wrong for a partial one: a
    transmittal the run never reached has no chunk count because it was never
    chunked. The scope is therefore narrowed to what was chunked, and the
    reconciliation ranges over the run's own work rather than over a corpus it
    did not finish.
    """
    counts = _chunks_by_document(evidence)
    reached = ExtractionScope(
        attempted=tuple(record for record in scope.attempted if record.document_id in counts),
        excluded=scope.excluded,
    )
    if not reached.attempted:
        return 0
    return attempted_invocation_count(reached, counts)


def _attempted_extractions(
    scope: ExtractionScope,
    evidence: RunEvidence,
    fields: Sequence[FieldTerm],
    results: Sequence[ExtractionStageResult],
) -> int:
    """FR-069's attempt total, derived from the corpus shape (`count_attempts`).

    Derived here and **not** summed from the resolutions the stage enumerated,
    which is what makes `AttemptLedger.unaccounted` a comparison of two
    derivations rather than a restatement of one.
    """
    counts = _chunks_by_document(evidence)
    reached = {
        result.document_id: counts[result.document_id]
        for result in results
        if result.document_id in counts
    }
    if not reached or not fields:
        return 0
    del scope  # the partition is already reflected in which documents have results
    return count_attempts(
        chunks_by_document=reached,
        attempted_fields=[term.name for term in fields],
        absent_fields_by_document={
            result.document_id: result.absent_fields
            for result in results
            if result.document_id in reached
        },
    )


def _aborted_at(failure: RunLevelFailure | None, detail: str | None) -> str | None:
    """What stopped the run, in one phrase, for the report driver's refusal."""
    if failure is not None:
        return f"{failure.kind} ({failure.document_id or 'no document in flight'})"
    return detail


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
        One of `EXIT_OK`, `EXIT_REFUSED` or `EXIT_ABORTED` — 0, 2 and 3, each
        documented beside its constant. Refusals are exit codes rather than
        tracebacks because this is a job an operator runs from a runbook.

    **What this entry drives, in order.** It selects the resolution mode and
    refuses a run whose invocations could not be priced (TR-048 — required in
    `replay` too), enumerates the committed corpus, verifies every content hash
    before anything is parsed (FR-005), mints every document identifier with the
    corpus-wide collision refusal that precedes the first transaction (FR-052),
    prints the enumeration and the extraction partition, and then runs the
    pipeline:
    `write_run_record` (FR-038, and FR-032's policy with it), `plan_documents`
    (FR-043), `write_generations` fed per document by `run_extraction_stage`
    through the writer's extraction hook, and `abort_run` or `finish_run`. The
    per-document disposition ledger (FR-073) is printed from what the run
    actually did, so a partial run reports itself as partial rather than as a
    smaller successful one.

    **With no fixtures committed (T081), `replay` is a partial run, and that is
    the designed outcome rather than a failure of it.** The 26 real
    specifications are excluded from extraction by FR-022 and commit their
    chunks; the first synthetic transmittal reaches `fixture_missing`, is
    recorded on the run record as one of FR-056's five, and is the ledger's one
    `rolled_back`; the transmittals behind it are `not_reached`. The exit code
    is 3, the four counts sum to 51, and every figure printed is what happened.

    **Offline in both modes, and the claim is structural.** Nothing in
    `model.ingest` imports `gateway`; the traced path is
    `model.llm.extraction`'s alone, it reaches the network only in `record`, and
    only with the opt-in set. In `replay` every response resolves from the
    committed store and a miss raises rather than falling back — asserted from
    the other side by `tests/checks/test_ingest_offline_only.py`, which refuses
    any import path from a request-serving entry point into this package.
    """
    arguments = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    # Imported here rather than at module scope: the corpus reader and the
    # connection are needed only by this entry, and a module-level import would
    # make every consumer of the ledger and scope types pay for them.
    import psycopg

    from model.ingest.documents import build_documents
    from model.ingest.manifest_reader import (
        ManifestReadError,
        iter_entries,
        manifest_digests,
        verify_hash,
    )
    from model.ingest.writer import connect

    try:
        resolve_resolution_mode(arguments.mode)
        require_price_table_pin()
    except OrchestrationError as error:
        print(f"ingest: {error}", file=sys.stderr)
        return EXIT_REFUSED

    try:
        documents = tuple(iter_entries(arguments.corpus_root))
        for document in documents:
            verify_hash(document)
        records = build_documents(documents)
        scope = select_extraction_documents(records)
        layers = documents_by_layer(records)
        digests = manifest_digests(arguments.corpus_root)
        trace = RunTrace.mint()
        # Read before the database is touched, so a build that cannot say what
        # produced it is refused before it writes a run record it could not
        # attribute (FR-038).
        build_revision()
    except (ManifestReadError, OrchestrationError, RunError, ValueError) as error:
        # FR-005 and FR-052 both abort **before** the first transaction, so no
        # run record exists yet to carry a run-level failure and nothing has been
        # written to roll back. The kinds those aborts are recorded under once a
        # run record does exist are `corpus_digest_mismatch` and
        # `document_id_collision` — built by the two constructors above, which is
        # why they take the subject FR-056 requires rather than a message.
        print(f"ingest: {error}", file=sys.stderr)
        return EXIT_REFUSED

    print(
        f"ingest: mode={arguments.mode} promote={arguments.promote} "
        f"documents={len(records)} REAL={layers[LAYER_REAL]} "
        f"SYNTHETIC={layers[LAYER_SYNTHETIC]} "
        f"extraction_attempted={len(scope.attempted)} excluded={len(scope.excluded)}"
    )

    try:
        with connect() as connection:
            outcome = run_ingestion(
                connection,
                records=records,
                scope=scope,
                mode=arguments.mode,
                trace_id=trace.trace_id,
                manifest_digests=digests,
                promote=arguments.promote,
            )
    except (OrchestrationError, RunError, WriterError, ValueError, psycopg.Error) as error:
        # Every refusal that reaches here precedes the first document: the
        # database was unreachable, the vocabulary was empty, the build had no
        # revision, or a resident generation this run may not replace was found
        # — all of them before `write_run_record`. A failure *after* documents
        # were written does not arrive as an exception; `run_ingestion` carries
        # it on the outcome, so a partial run is reported as one rather than as
        # a refusal. The single exception is a disposition ledger that does not
        # partition the corpus, which is a defect in this module rather than an
        # operational state and is reported as the refusal it is.
        print(f"ingest: {error}", file=sys.stderr)
        return EXIT_REFUSED

    counts = outcome.ledger.counts
    print(f"ingest: run_id={outcome.run_id} trace_id={trace.trace_id}")
    print(
        "ingest: dispositions "
        + " ".join(f"{name}={counts[name]}" for name in DISPOSITIONS)
        + f" enumerated={outcome.ledger.population}"
    )
    print(
        f"ingest: written chunks={outcome.chunks_written} values={outcome.values_written} "
        f"failures={outcome.failures_written} invocations={outcome.invocations}"
    )
    if outcome.publication is not None:
        # FR-071's outcome, emitted or refused, and never silence. A refusal
        # names the items, what obliges each, and why each has no data — which is
        # the difference between a report that was not written and a report
        # nobody tried to write.
        print(f"ingest: {outcome.publication.rendered()}")
    if outcome.failure is not None:
        print(
            f"ingest: aborted — {outcome.failure.kind}: {outcome.failure.recorded_detail}",
            file=sys.stderr,
        )
    elif outcome.detail is not None:
        print(
            f"ingest: aborted with no run-level kind recorded — {outcome.detail}. The run "
            f"reads as `in_flight`; see `run_ingestion` for why FR-056's closed five has no "
            f"member for it.",
            file=sys.stderr,
        )
    return outcome.exit_code
