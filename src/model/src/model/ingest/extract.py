"""The extraction stage: the caller `extract_fields` did not have until now.

T093, FR-025 / FR-026 / FR-030. Every piece this module drives was built and
tested on its own — `model.llm.extraction` issues the traced invocation (T040),
`model.compute.coerce` types the printed value (T049), `model.compute.confidence`
scores it (T057), `writer.cite_value` anchors it (T066), `lineitems.group_line_items`
groups it (T071) and `failures.absent_field_records` records what was not printed
(T060). What did not exist was anything that called them in order, so
`extract_fields` had no production caller anywhere in the repository. This module
is that order, and it is deliberately assembly and nothing else: it computes no
coercion, no score, no citation rule and no grouping of its own.

**Extraction runs inside the per-document transaction, and this module is not
what opens it** (HINT-002, `data-model.md` §Write Order). The stage produces the
value, failure and membership sequences `writer.write_document_generation` takes
as arguments; that function opens the one transaction and writes them at the
steps the write order fixes — values after the chunks, contributing chunks after
the values, line items and parse signals after the run associations. Nothing
here reorders that, and nothing here holds a connection.

**The two failure classes stay where FR-056 put them.** An
`ExtractionRunFailure` — a missing fixture in `replay`, an unreachable provider —
is re-raised untouched, because it aborts the run and is recorded on
`ingestion_run` in a fresh transaction *after* the rollback, which is the caller's
job and cannot be this one's. An `ExtractionSchemaViolation` is per-field and
becomes `extraction_failure` rows here. A stage that caught both would turn a
broken configuration into a document full of failure rows and let the run
continue.

**No fixtures are committed (T081), so a `replay` run reaches
`fixture_missing`.** That is the designed behaviour rather than a defect in it:
the miss surfaces as `ExtractionRunFailure(RUN_FAILURE_FIXTURE_MISSING, …)`,
travels out of this function unchanged, and `ingest/cli.py`'s `fixture_missing`
and `abort_run` record it as one of FR-056's five run-level kinds. The unit tests
drive this module through an **injected invoker**, the same seam
`src/model/tests/llm/test_extraction.py` uses, so the assembly is exercised
without a provider and without a fixture store.

**One invocation per chunk** (FR-069). The invocation is the unit, the chunk is
what it covers, and a value's citation is inherited from the chunk it was read
out of — never asserted by the model, which is never asked for a page number.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Final

from model.compute.coerce import CoercionError, coerce_value
from model.compute.confidence import (
    LABEL_MATCH_ALTERNATE,
    LABEL_MATCH_CANONICAL,
    ParseSignals,
    compute_confidence,
)
from model.corpus.codes import VOCABULARY, fold_label
from model.ingest.chunker import Chunk
from model.ingest.failures import (
    AttemptedChunk,
    ExtractionFailure,
    absent_field_records,
)
from model.ingest.lineitems import (
    GroupedValue,
    LineItemMembership,
    group_line_items,
)
from model.ingest.runs import ConfidencePolicy
from model.ingest.writer import CitedChunk, PreparedValue, cite_value
from model.llm.extraction import (
    ExtractionChunk,
    ExtractionOutcome,
    ExtractionRunFailure,
    ExtractionSchemaViolation,
    Invoker,
    extract_fields,
)
from model.llm.schemas import DOCUMENT_SCOPE, FieldTerm, bound_field_names

__all__ = [
    "OUTCOME_CONFIDENCE_BELOW_THRESHOLD",
    "OUTCOME_SCHEMA_VIOLATION",
    "OUTCOME_TYPE_COERCION_FAILED",
    "ExtractionStageError",
    "ExtractionStageResult",
    "canonical_labels",
    "classify_label",
    "run_extraction_stage",
]

#: The three members of `ck_extraction_failure__outcome`'s closed seven this
#: stage can produce, beside `no_value_found` — which `failures.absent_field_records`
#: owns — and `repair_budget_exhausted`, which arrives on the error the gateway
#: raised rather than being decided here. Named rather than written inline so the
#: row a branch writes and the outcome a test asserts are one string.
OUTCOME_SCHEMA_VIOLATION: Final[str] = "schema_violation"
OUTCOME_TYPE_COERCION_FAILED: Final[str] = "type_coercion_failed"
OUTCOME_CONFIDENCE_BELOW_THRESHOLD: Final[str] = "confidence_below_threshold"


class ExtractionStageError(RuntimeError):
    """The stage cannot run over this document as described.

    One type, and every one of them means the same thing: no value and no
    failure row is produced for this document, because producing either would
    record an outcome for work that was never well-defined. It is **not** one of
    FR-056's run-level kinds and it is **not** a per-field outcome — it is a
    caller error, raised before any invocation is issued so it costs nothing.
    """


@cache
def canonical_labels() -> frozenset[str]:
    """Every canonical label of the committed vocabulary, folded for comparison.

    Built from `model.corpus.codes.VOCABULARY` — committed *data* naming what a
    field may be called — rather than restated here, and folded through
    `fold_label` so the comparison is the one disjointness was asserted in when
    the vocabulary was read. A second folding written here would be a second
    definition of "the same label", and the two could disagree about a pair the
    vocabulary reader had already admitted.
    """
    return frozenset(
        fold_label(VOCABULARY.labels(key).canonical_label) for key in VOCABULARY.field_keys
    )


def classify_label(printed_label: str, term: FieldTerm) -> str:
    """FR-057's first signal: `canonical` or `alternate`, and nothing else.

    Args:
        printed_label: the label text exactly as printed beside the value, as
            the model reported it. Reported rather than classified by the model
            (FR-031) — this is the deterministic code that classifies it.
        term: the seeded vocabulary term the value is stored under. Its `label`
            is E003's own, which is what the prompt showed the model.

    Returns:
        `canonical` when the folded printed label is a canonical label — the
        term's own, or any of the committed vocabulary's — and `alternate`
        otherwise.

    **A label matching nothing is `alternate`, not a third value.**
    `ck_extracted_value_parse_signal__label_match` fixes the domain at two, so
    there is no room for "unrecognised"; and the deduction FR-057 attaches to
    this signal is for a label that was *not* the canonical form, which an
    unrecognised label also is not. Mapping it to `canonical` would be the one
    direction that loses information — it would score an unrecognisable label as
    confidently as a perfect one.
    """
    folded = fold_label(printed_label)
    if folded == fold_label(term.label) or folded in canonical_labels():
        return LABEL_MATCH_CANONICAL
    return LABEL_MATCH_ALTERNATE


@dataclass(frozen=True)
class ExtractionStageResult:
    """One document's extraction, in the shapes the writer and the report take.

    `values`, `failures` and `line_items` are exactly
    `writer.write_document_generation`'s three sequences, and `line_items` is
    indexed by position into `values` — so the caller passes them straight
    through rather than reordering anything the write order fixes.

    The three invocation counts are FR-069's invocation-level ledger for this
    document, taken from the gateway's own outcome on each result rather than
    counted by the loop that issued the calls. `absent_fields` is what
    `ingest/cli.py` feeds `count_attempts`, where a field printed nowhere in the
    document collapses from one attempt per chunk to one attempt for the
    document.
    """

    document_id: str
    values: tuple[PreparedValue, ...]
    failures: tuple[ExtractionFailure, ...]
    line_items: tuple[LineItemMembership, ...]
    invocations_valid: int
    invocations_repaired: int
    invocations_failed: int
    absent_fields: tuple[str, ...]

    @property
    def invocations(self) -> int:
        return self.invocations_valid + self.invocations_repaired + self.invocations_failed

    @property
    def fields_with_values(self) -> tuple[str, ...]:
        """Attempted terms this document stored at least one value for, sorted."""
        return tuple(sorted({value.field_name for value in self.values}))


def _extraction_chunk(chunk: Chunk) -> ExtractionChunk:
    """The invocation's view of one chunk.

    `chunk_id` is the chunker's own `document:ordinal` handle rather than a
    database identifier, and that is forced rather than chosen: chunk
    identifiers are minted by `uuid4()` inside the document's transaction at
    §Write Order step 1, so nothing upstream of the write can know one. The
    ordinal is what every citation and every failure row carries from here to
    the write, and it is unique within the document by
    `uq_chunk__document_ordinal`. The field takes no part in the request the
    gateway hashes, so nothing about a fixture key depends on this.
    """
    return ExtractionChunk(
        document_id=chunk.document_id,
        chunk_id=f"{chunk.document_id}:{chunk.ordinal}",
        ordinal=chunk.ordinal,
        page_number=chunk.page_number,
        body_text=chunk.body_text,
    )


def _label_chunk(printed_label: str, anchor: Chunk, chunks: Sequence[Chunk]) -> CitedChunk | None:
    """The chunk carrying only the label, where the field split across a page.

    Args:
        printed_label: the label as printed beside the value.
        anchor: the chunk carrying the **printed value**, which is the citation
            anchor (FR-029).
        chunks: the document's chunks, in ordinal order.

    Returns:
        The nearest preceding chunk on a **strictly earlier page** whose text
        carries the label, or `None` — which is the ordinary case, since most
        values are printed with their label in one chunk.

    **Derived from the chunk text and from nothing else.** The page-split shape
    is a label ending page *k* and its value opening page *k+1*, so the evidence
    is that the anchor chunk does not carry the label and an earlier page's
    chunk does. No template, no pre-render model and no answer key is consulted;
    the model is not asked either, because it is never asked for a page.

    **Strictly earlier page, nearest first.** `ValueCitation` refuses a
    contributor on a page after the anchor's, so the earlier-page condition is
    the constraint restated where the candidate is chosen rather than left to
    fail at construction. Nearest-first bounds a common label word to the chunk
    that actually preceded the value.
    """
    folded_label = fold_label(printed_label)
    if not folded_label or folded_label in fold_label(anchor.body_text):
        return None
    preceding = [
        chunk
        for chunk in chunks
        if chunk.ordinal < anchor.ordinal and chunk.page_number < anchor.page_number
    ]
    for chunk in sorted(preceding, key=lambda entry: entry.ordinal, reverse=True):
        if folded_label in fold_label(chunk.body_text):
            return CitedChunk(ordinal=chunk.ordinal, page_number=chunk.page_number)
    return None


@dataclass(frozen=True)
class _Candidate:
    """One value that survived coercion and scoring, before grouping decides it."""

    value: PreparedValue
    item_ordinal: int


def _chunk_failure(
    chunk: Chunk, field_name: str, outcome: str, detail: str, repair_attempt_count: int = 0
) -> ExtractionFailure:
    return ExtractionFailure(
        source_chunk=AttemptedChunk(ordinal=chunk.ordinal, page_number=chunk.page_number),
        field_name=field_name,
        outcome=outcome,
        repair_attempt_count=repair_attempt_count,
        detail=detail,
    )


def _invocation_failures(
    chunk: Chunk, fields: Sequence[FieldTerm], error: ExtractionSchemaViolation
) -> tuple[ExtractionFailure, ...]:
    """One row per field the refused invocation was covering (FR-034, FR-069).

    An invocation covers a chunk's whole declared field subset, so an invocation
    that produced nothing schema-valid failed every one of those attempts. One
    row for the chunk would leave the rest of them resolving to neither a stored
    value nor a failure, which is precisely the unaccounted attempt FR-069's
    ledger exists to make visible.

    The outcome and the repair count are read off the error rather than decided
    here: `repair_budget_exhausted` when the single repair was spent and
    `schema_violation` when the output was refused without one, which is the
    gateway's own `repair_attempt_count` and not a second judgement.
    """
    return tuple(
        _chunk_failure(
            chunk,
            term.name,
            error.outcome,
            (
                f"the invocation covering ordinal {chunk.ordinal} of {chunk.document_id} "
                f"produced no schema-valid output, so {term.name} was attempted and not "
                f"read: {error.detail}"
            ),
            repair_attempt_count=error.repair_attempt_count,
        )
        for term in fields
    )


def _values_from_outcome(
    outcome: ExtractionOutcome,
    chunk: Chunk,
    chunks: Sequence[Chunk],
    terms: Mapping[str, FieldTerm],
    fields: Sequence[FieldTerm],
    policy: ConfidencePolicy,
) -> tuple[tuple[_Candidate, ...], tuple[ExtractionFailure, ...]]:
    """Coerce, cite and score everything one invocation returned.

    Every returned value leaves here as exactly one of two things: a candidate
    for storage, or a failure row. There is no third branch and nothing is
    dropped — a value that vanished between the model and the database would be
    an attempt with no resolution, and nothing downstream could tell it from a
    field the document never printed.
    """
    accepted, refused = bound_field_names((entry.field_name for entry in outcome.values), fields)
    candidates: list[_Candidate] = []
    failures: list[ExtractionFailure] = []

    for name in refused:
        # FR-024: a name outside the run's attempted subset is a per-field
        # refusal, recorded rather than discarded — the vocabulary is not
        # widened at run time and a silently dropped name is a widening nobody
        # can see.
        failures.append(
            _chunk_failure(
                chunk,
                name,
                OUTCOME_SCHEMA_VIOLATION,
                (
                    f"{name!r} is outside the {len(fields)} terms this run attempted "
                    f"(FR-024). The seeded vocabulary is not widened at run time, so the "
                    f"value is refused rather than stored under a term nobody declared."
                ),
            )
        )
    if not accepted:
        return (), tuple(failures)

    admissible = set(accepted)
    for entry in outcome.values:
        if entry.field_name not in admissible:
            continue
        term = terms[entry.field_name]
        try:
            coerced = coerce_value(entry.value_text, term.value_kind)
        except CoercionError as error:
            failures.append(
                _chunk_failure(
                    chunk,
                    term.name,
                    OUTCOME_TYPE_COERCION_FAILED,
                    f"{term.name} on ordinal {chunk.ordinal}: {error}",
                )
            )
            continue

        contributor = _label_chunk(entry.printed_label, chunk, chunks)
        citation = cite_value(
            CitedChunk(ordinal=chunk.ordinal, page_number=chunk.page_number),
            () if contributor is None else (contributor,),
        )
        signals = ParseSignals(
            label_match=classify_label(entry.printed_label, term),
            source_chunk_count=citation.source_chunk_count,
            validated_after_repair=outcome.repaired,
        )
        confidence = compute_confidence(signals, policy.weights)
        if not policy.admits(confidence):
            # FR-032: recorded absent rather than stored wrong. The failure row
            # carries no value and no confidence — `ExtractionFailure` has no
            # field for either — so the score below is named in prose only.
            failures.append(
                _chunk_failure(
                    chunk,
                    term.name,
                    OUTCOME_CONFIDENCE_BELOW_THRESHOLD,
                    (
                        f"{term.name} on ordinal {chunk.ordinal} scored below this run's "
                        f"declared floor of {policy.floor!r} under its signals "
                        f"({signals.description}). A value below the floor is recorded as "
                        f"a failure and is not persisted."
                    ),
                )
            )
            continue

        candidates.append(
            _Candidate(
                value=PreparedValue(
                    field_name=term.name,
                    value_kind=coerced.value_kind,
                    value_text=coerced.value_text,
                    value_number=coerced.value_number,
                    confidence=confidence,
                    citation=citation,
                    signals=signals,
                ),
                item_ordinal=entry.item_ordinal,
            )
        )

    return tuple(candidates), tuple(failures)


def run_extraction_stage(
    *,
    document_id: str,
    chunks: Sequence[Chunk],
    fields: Sequence[FieldTerm],
    run_id: str,
    trace_id: str,
    policy: ConfidencePolicy,
    model: str | None = None,
    invoke: Invoker | None = None,
) -> ExtractionStageResult:
    """Extract one document, end to end, and return what the write takes.

    Args:
        document_id: the document being extracted, as `mint_document_id` minted
            it. Checked against the chunks rather than trusted, because a stage
            run over another document's chunks would cite ordinals this
            document's transaction never writes.
        chunks: every chunk of the document, in ordinal order. One invocation is
            issued per chunk (FR-069).
        fields: the run's attempted subset — `schemas.attempted_terms` filtered
            to the terms unretired at run time (FR-024, FR-058). Declared before
            the run and the same for every document, so the absence records and
            the attempt ledger denominate on one set.
        run_id: the `ingestion_run` this generation belongs to. Carried onto
            every line-item membership, where the association's composite
            foreign key holds it equal to the value's own attribution.
        trace_id: the run's single trace identifier (FR-070), passed explicitly
            on every invocation because the gateway reads no ambient context.
        policy: the run's floor and three deduction weights, read from its own
            `ingestion_run` row. Passed rather than looked up: this module holds
            no connection, and a score is only checkable against the policy the
            caller can name.
        model: the provider model, or `None` for the gateway's default.
        invoke: the gateway entry point. Defaults to the single traced path;
            supplied by tests, which is the seam that lets this stage be driven
            with no provider and no fixture store.

    Returns:
        The values, failure rows and line-item memberships for this document,
        with the invocation counts and the fields it printed nowhere.

    Raises:
        ExtractionStageError: no chunk, no attempted field, or a chunk belonging
            to another document. Each is refused before the first invocation, so
            it costs nothing and produces no partial document.
        ExtractionRunFailure: a missing fixture in `replay` or an unreachable
            provider (FR-056). **Re-raised untouched**: it aborts the run and is
            recorded on `ingestion_run` after the rollback, in a fresh
            transaction, which is the caller's job. With no fixtures committed
            (T081) this is what a `replay` run reaches, and reaching it cleanly
            is the designed behaviour rather than a failure of it.

    **The order is the one the pieces were built for.** Per chunk: one traced
    invocation; every returned name bound against the attempted subset; every
    bound value coerced to its term's kind; the citation anchored on the chunk
    that printed it, with the label's chunk as a contributor where the field
    split across a page; the three parse signals assembled and the confidence
    computed from them under this run's weights; the floor applied at the
    storage boundary. Then, per document: the line-item memberships, and one
    `no_value_found` record for each attempted field the document printed
    nowhere.

    **Grouping runs after scoring and before the return, and it can still
    refuse.** A value whose reported ordinal contradicts its field's declared
    scope earns a failure row instead of a membership, and the surviving values
    are re-indexed so every membership's position still addresses the value it
    belongs to. Positions are the writer's only handle — value identifiers are
    minted inside the transaction — so a stale index would attach a membership
    to the wrong row rather than to none.
    """
    if not chunks:
        raise ExtractionStageError(
            f"{document_id} reached extraction with zero chunks. A document with no chunk "
            f"has nothing to attempt a field on, and a `no_value_found` record for it "
            f"could name no source chunk — `extraction_failure.source_chunk_id` is NOT "
            f"NULL. That is a run-level condition, not a document with ten absences."
        )
    if not fields:
        raise ExtractionStageError(
            f"{document_id} reached extraction with zero attempted fields (FR-024). Every "
            f"field would be unattempted and the document would report no values found, "
            f"which is the shape of a total failure wearing the shape of a clean run."
        )
    foreign = sorted({chunk.document_id for chunk in chunks} - {document_id})
    if foreign:
        raise ExtractionStageError(
            f"the extraction stage for {document_id} was handed chunks of {foreign}. A "
            f"citation carries a chunk ordinal, and an ordinal of another document "
            f"resolves inside this document's transaction to a chunk that prints "
            f"something else."
        )

    terms = {term.name: term for term in fields}
    ordered = sorted(chunks, key=lambda chunk: chunk.ordinal)
    candidates: list[_Candidate] = []
    failures: list[ExtractionFailure] = []
    valid = repaired = failed = 0

    for chunk in ordered:
        try:
            outcome = extract_fields(
                _extraction_chunk(chunk),
                fields,
                trace_id=trace_id,
                model=model,
                invoke=invoke,
            )
        except ExtractionRunFailure:
            # FR-056. Untouched and unwrapped: the run aborts, and the kind and
            # detail this carries are what `ingest/cli.py` records on
            # `ingestion_run` in a fresh transaction after the rollback.
            raise
        except ExtractionSchemaViolation as error:
            failed += 1
            failures.extend(_invocation_failures(chunk, fields, error))
            continue

        repaired += 1 if outcome.repaired else 0
        valid += 0 if outcome.repaired else 1
        produced, refusals = _values_from_outcome(outcome, chunk, ordered, terms, fields, policy)
        candidates.extend(produced)
        failures.extend(refusals)

    document_scoped = {term.name for term in fields if term.scope == DOCUMENT_SCOPE}
    grouping = group_line_items(
        (
            GroupedValue(
                position=position,
                field_name=candidate.value.field_name,
                reported_ordinal=candidate.item_ordinal,
            )
            for position, candidate in enumerate(candidates)
        ),
        run_id=run_id,
        document_id=document_id,
        document_scoped_fields=document_scoped,
    )

    refused_positions = {refusal.position for refusal in grouping.refusals}
    for refusal in grouping.refusals:
        candidate = candidates[refusal.position]
        failures.append(
            ExtractionFailure(
                source_chunk=AttemptedChunk(
                    ordinal=candidate.value.citation.anchor.ordinal,
                    page_number=candidate.value.citation.anchor.page_number,
                ),
                field_name=refusal.field_name,
                outcome=OUTCOME_SCHEMA_VIOLATION,
                repair_attempt_count=0,
                detail=refusal.reason,
            )
        )

    # Re-indexed against the values that survive, because the membership's
    # `position` is an index into the sequence handed to the writer and the
    # refused values are not in it.
    kept = [
        (position, candidate)
        for position, candidate in enumerate(candidates)
        if position not in refused_positions
    ]
    reindexed = {old: new for new, (old, _) in enumerate(kept)}
    values = tuple(candidate.value for _, candidate in kept)
    line_items = tuple(
        LineItemMembership(
            position=reindexed[member.position],
            run_id=member.run_id,
            document_id=member.document_id,
            item_ordinal=member.item_ordinal,
        )
        for member in grouping.memberships
    )

    stored_fields = {value.field_name for value in values}
    absences = absent_field_records(
        document_id=document_id,
        attempted_fields=[term.name for term in fields],
        attempted_chunks=[
            AttemptedChunk(ordinal=chunk.ordinal, page_number=chunk.page_number)
            for chunk in ordered
        ],
        fields_with_values=stored_fields,
    )
    # A field that produced a value the floor rejected, or one the coercion
    # refused, already carries a row explaining that. Recording it absent as
    # well would say the document did not print it, which is the opposite of
    # what happened, and would resolve one attempt twice in FR-069's ledger.
    already_recorded = {failure.field_name for failure in failures}
    absences = tuple(record for record in absences if record.field_name not in already_recorded)
    failures.extend(absences)

    return ExtractionStageResult(
        document_id=document_id,
        values=values,
        failures=tuple(failures),
        line_items=line_items,
        invocations_valid=valid,
        invocations_repaired=repaired,
        invocations_failed=failed,
        absent_fields=tuple(record.field_name for record in absences),
    )
