"""T093: the extraction stage, driven through the injected invoker.

`extract_fields` had no production caller until `ingest/extract.py` landed —
T040 built the traced path, T042 restricted its scope, T046 placed its citation
and T092 covered it, and nothing invoked it. So what is asserted here is
specifically the **assembly**: that a chunk becomes an invocation, that what
comes back is bound against the attempted subset, coerced by
`model.compute.coerce`, anchored by `writer.cite_value`, scored by
`model.compute.confidence` under the run's own weights, grouped by
`lineitems.group_line_items`, and arrives in the three sequences
`writer.write_document_generation` takes.

**The seam is the one `tests/llm/test_extraction.py` already uses.** The invoker
is injected as a parameter rather than patched onto a module global, so every
assertion below runs against the real `extract_fields`, the real
`InvocationRequest`, and the real schema validation — the substitution is the
provider, not the path to it. That is also what lets the whole stage be
exercised with no fixture store and no provider, which matters because **zero
extraction fixtures are committed** (T081): the fixture-less `replay` case is
asserted here as reaching `fixture_missing` cleanly rather than crashing.

**Every value the stage produces is checked against the writer's own guard.**
`writer.check_confidence_agrees` is what the write runs before a row is stored,
and running it here closes the loop: a stage that computed a confidence its
signals do not recompute to would fail at the boundary rather than in a test
that agreed with it by construction.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from gateway.api import InvocationRequest, InvocationResult
from gateway.errors import GatewayValidationError, ProviderUnavailableError
from gateway.fixtures import FixtureMissError
from gateway.validation import MAX_REPAIR_ATTEMPTS

from model.compute.confidence import (
    LABEL_MATCH_ALTERNATE,
    LABEL_MATCH_CANONICAL,
    DeductionWeights,
)
from model.ingest.chunker import Chunk
from model.ingest.extract import (
    OUTCOME_CONFIDENCE_BELOW_THRESHOLD,
    OUTCOME_SCHEMA_VIOLATION,
    OUTCOME_TYPE_COERCION_FAILED,
    ExtractionStageError,
    classify_label,
    run_extraction_stage,
)
from model.ingest.runs import (
    DECLARED_CONFIDENCE_FLOOR,
    DECLARED_DEDUCTION_ALTERNATE_LABEL,
    DECLARED_DEDUCTION_PAGE_SPLIT,
    DECLARED_DEDUCTION_REPAIRED,
    ConfidencePolicy,
)
from model.ingest.writer import check_confidence_agrees
from model.llm.extraction import RUN_FAILURE_FIXTURE_MISSING, ExtractionRunFailure
from model.llm.schemas import TRANSMITTAL_FIELD_SUBSET, term

DOCUMENT_ID = "prj-001-t0001-r0"
RUN_ID = "00000000-0000-4000-8000-00000000e006"
TRACE_ID = "0123456789abcdef0123456789abcdef"

#: The run's declared policy, constructed here from `runs.py`'s own constants
#: rather than from numbers typed into this file — the deductions decide which
#: of the eight combinations land below the floor, and a second copy of them
#: would make these tests agree with a policy the run never had.
POLICY = ConfidencePolicy(
    floor=DECLARED_CONFIDENCE_FLOOR,
    weights=DeductionWeights(
        alternate_label=DECLARED_DEDUCTION_ALTERNATE_LABEL,
        page_split=DECLARED_DEDUCTION_PAGE_SPLIT,
        repaired=DECLARED_DEDUCTION_REPAIRED,
    ),
)

#: The four attempted terms these tests use: one text, one number, one date, and
#: one document-scoped. Narrower than the committed ten on purpose — a document
#: printing none of the other six would otherwise bury every assertion under six
#: `no_value_found` records.
FIELDS = (
    term("manufacturer"),
    term("quantity"),
    term("submittal_date"),
    term("submittal_number"),
)


def chunk(ordinal: int, *, page: int = 1, body: str = "Manufacturer: Nordway") -> Chunk:
    return Chunk(
        document_id=DOCUMENT_ID,
        document_type="transmittal",
        project_id="PRJ-001",
        page_number=page,
        ordinal=ordinal,
        body_text=body,
        boundary_class="structural",
        structural_identifier=f"p{page}-body{ordinal}",
        content_pieces=12,
    )


class ScriptedInvoker:
    """Answers each invocation in turn, by chunk ordinal.

    Keyed on the order the calls arrive rather than on the request's contents,
    so a stage that issued the wrong number of invocations shows up as an
    exhausted script instead of as a silently repeated answer.
    """

    def __init__(self, *replies: str | Exception, outcome: str = "valid") -> None:
        self.replies = list(replies)
        self.outcome = outcome
        self.seen: list[InvocationRequest] = []

    def __call__(self, request: InvocationRequest) -> InvocationResult:
        self.seen.append(request)
        if not self.replies:
            raise AssertionError(
                f"the stage issued {len(self.seen)} invocations and the script holds "
                f"{len(self.seen) - 1}"
            )
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return InvocationResult(
            invocation_id=f"a3f1c0de-0000-4000-8000-{len(self.seen):012d}",
            trace_id=request.trace_id or TRACE_ID,
            content=reply,
            outcome=self.outcome,
            resolution_mode="replay",
        )


def values_json(*entries: str) -> str:
    return '{"values": [' + ", ".join(entries) + "]}"


def value(field: str, label: str, text: str, ordinal: int = 1) -> str:
    return (
        f'{{"field_name": "{field}", "printed_label": "{label}", '
        f'"value_text": "{text}", "item_ordinal": {ordinal}}}'
    )


def stage(*replies: str | Exception, chunks=None, fields=FIELDS, outcome: str = "valid"):
    invoker = ScriptedInvoker(*replies, outcome=outcome)
    result = run_extraction_stage(
        document_id=DOCUMENT_ID,
        chunks=chunks if chunks is not None else [chunk(0)],
        fields=fields,
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        policy=POLICY,
        invoke=invoker,
    )
    return result, invoker


# ---------------------------------------------------------------------------
# The assembly itself — one invocation per chunk, under the run's trace id
# ---------------------------------------------------------------------------


def test_one_invocation_is_issued_per_chunk_under_the_runs_trace_id() -> None:
    """FR-069's invocation unit, and FR-070's single identifier.

    The trace identifier is asserted on the request the gateway would have
    received rather than on the result, because the gateway reads no ambient
    context: a stage that minted one per call would still produce results that
    carried an identifier.
    """
    chunks = [chunk(0), chunk(1, page=2, body="Qty.: 12"), chunk(2, page=2)]
    result, invoker = stage(values_json(), values_json(), values_json(), chunks=chunks)
    assert len(invoker.seen) == 3
    assert {request.trace_id for request in invoker.seen} == {TRACE_ID}
    assert result.invocations == 3
    assert result.invocations_valid == 3


def test_a_returned_value_is_coerced_cited_scored_and_grouped() -> None:
    """The whole chain, on one value, asserted at each link."""
    result, _ = stage(values_json(value("quantity", "Quantity", "1,250")))
    assert len(result.values) == 1
    stored = result.values[0]
    assert stored.field_name == "quantity"
    assert stored.value_kind == "number"
    # `model.compute.coerce`'s canonical form and typed numeric, not the stage's.
    # The printed grouping survives in `value_text` — it is the evidence the
    # citation points at (FR-027, FR-062) — and the typed form travels beside it.
    assert stored.value_text == "1,250"
    assert stored.value_number == Decimal("1250")
    # FR-029: inherited from the chunk, never supplied.
    assert stored.citation.cited_page == 1
    assert stored.citation.anchor.ordinal == 0
    assert stored.citation.source_chunk_count == 1
    # FR-030 / FR-057: no deduction fired, so the score is 1.0 exactly.
    assert stored.signals.label_match == LABEL_MATCH_CANONICAL
    assert stored.confidence == 1.0
    # FR-059: one membership, addressing this value by position.
    assert [(member.position, member.item_ordinal) for member in result.line_items] == [(0, 1)]
    assert result.line_items[0].run_id == RUN_ID


def test_every_stored_value_satisfies_the_writers_own_confidence_guard() -> None:
    """SC-026 at the boundary the write actually checks it at.

    `check_confidence_agrees` recomputes the score from the stored signals under
    the run's weights and demands **bit equality**, then applies the floor. A
    stage whose arithmetic differed from the writer's would fail here rather
    than at a write nobody in this suite performs.
    """
    result, _ = stage(
        values_json(
            value("manufacturer", "Mfr", "Nordway Fabrication"),
            value("quantity", "Quantity", "12"),
        )
    )
    assert result.values
    for stored in result.values:
        check_confidence_agrees(stored, POLICY)


# ---------------------------------------------------------------------------
# FR-024 / FR-034 — every attempt resolves, and to one of the closed seven
# ---------------------------------------------------------------------------


def test_a_name_outside_the_attempted_subset_is_refused_and_not_stored() -> None:
    """FR-024: the vocabulary is not widened at run time."""
    result, _ = stage(
        values_json(
            value("manufacturer", "Manufacturer", "Nordway"),
            value("unit_price", "Unit Price", "412.00"),
        )
    )
    assert [stored.field_name for stored in result.values] == ["manufacturer"]
    refusal = next(entry for entry in result.failures if entry.field_name == "unit_price")
    assert refusal.outcome == OUTCOME_SCHEMA_VIOLATION
    assert "FR-024" in refusal.detail


def test_a_value_outside_its_kinds_accepted_forms_is_a_coercion_failure() -> None:
    """FR-049 / FR-037: recorded absent, never inferred, and never stored."""
    result, _ = stage(values_json(value("quantity", "Quantity", "TBD")))
    assert result.values == ()
    failure = next(entry for entry in result.failures if entry.field_name == "quantity")
    assert failure.outcome == OUTCOME_TYPE_COERCION_FAILED
    assert failure.source_chunk.ordinal == 0
    assert failure.attempted_page == 1


def test_a_score_below_the_runs_floor_is_recorded_and_not_persisted() -> None:
    """FR-032. A repaired invocation alone drops below the declared 0.80 floor.

    Driven through the gateway's *own* outcome rather than a flag on the stage:
    `validated_after_repair` is FR-057's third signal and is read off the
    invocation result, so this exercises the same path a real repair would.
    """
    result, _ = stage(
        values_json(value("manufacturer", "Manufacturer", "Nordway")), outcome="repaired"
    )
    assert result.invocations_repaired == 1
    assert result.values == ()
    failure = next(entry for entry in result.failures if entry.field_name == "manufacturer")
    assert failure.outcome == OUTCOME_CONFIDENCE_BELOW_THRESHOLD
    assert str(DECLARED_CONFIDENCE_FLOOR) in failure.detail
    # FR-036: a failure carries no value and no confidence, and there is no
    # field on the record for either.
    assert not hasattr(failure, "value_text")


def test_a_refused_invocation_fails_every_field_it_was_covering() -> None:
    """FR-069: one row per attempt, so the ledger has nothing unaccounted for.

    An invocation covers a chunk's whole declared subset. One row for the chunk
    would leave the other three attempts resolving to neither a value nor a
    failure, which is exactly the hole the ledger exists to expose.
    """
    error = GatewayValidationError(
        "no schema-valid output", field_paths=("values.0.value_text",), repair_attempt_count=1
    )
    result, _ = stage(error)
    assert result.invocations_failed == 1
    assert result.values == ()
    assert {entry.field_name for entry in result.failures} == {entry.name for entry in FIELDS}
    for entry in result.failures:
        assert entry.repair_attempt_count == MAX_REPAIR_ATTEMPTS
        assert entry.outcome == "repair_budget_exhausted"


def test_a_refusal_with_no_repair_spent_is_a_schema_violation() -> None:
    """FR-026's two outcomes are not the same row, and the split is the count."""
    error = GatewayValidationError("refused", field_paths=(), repair_attempt_count=0)
    result, _ = stage(error)
    assert {entry.outcome for entry in result.failures} == {OUTCOME_SCHEMA_VIOLATION}


# ---------------------------------------------------------------------------
# FR-037 / FR-058 — absence is recorded once per document, and only when absent
# ---------------------------------------------------------------------------


def test_a_field_printed_nowhere_is_recorded_once_for_the_document() -> None:
    chunks = [chunk(0), chunk(1, page=2)]
    result, _ = stage(
        values_json(value("manufacturer", "Manufacturer", "Nordway")),
        values_json(),
        chunks=chunks,
    )
    expected = ["quantity", "submittal_date", "submittal_number"]
    absent = [entry for entry in result.failures if entry.outcome == "no_value_found"]
    assert [entry.field_name for entry in absent] == expected
    assert result.absent_fields == tuple(expected)
    # The stated convention: the lowest-ordinal chunk the field was attempted on.
    assert {entry.source_chunk.ordinal for entry in absent} == {0}


def test_a_field_that_failed_for_another_reason_is_not_also_recorded_absent() -> None:
    """Two rows for one field would resolve one attempt twice.

    `quantity` was printed and refused by coercion. Recording it absent as well
    would say the document did not print it, which is the opposite of what
    happened.
    """
    result, _ = stage(values_json(value("quantity", "Quantity", "TBD")))
    quantity_rows = [entry for entry in result.failures if entry.field_name == "quantity"]
    assert [entry.outcome for entry in quantity_rows] == [OUTCOME_TYPE_COERCION_FAILED]
    assert "quantity" not in result.absent_fields


# ---------------------------------------------------------------------------
# FR-059 — the grouping, and the re-indexing a refusal forces
# ---------------------------------------------------------------------------


def test_a_document_scoped_field_takes_ordinal_zero_whatever_the_model_reported() -> None:
    """The group is a property of the field, and the model is not asked."""
    result, _ = stage(values_json(value("submittal_number", "Transmittal No.", "T0001", ordinal=4)))
    assert [member.item_ordinal for member in result.line_items] == [0]


def test_an_item_scoped_field_reporting_ordinal_zero_is_refused_and_dropped() -> None:
    """And the surviving memberships are re-indexed onto the values that remain.

    The membership's `position` is an index into the sequence handed to the
    writer, and the refused value is not in it. A stale index would attach the
    membership to the wrong row rather than to none — which the database would
    accept, because both rows exist.
    """
    result, _ = stage(
        values_json(
            value("manufacturer", "Manufacturer", "Nordway", ordinal=0),
            value("quantity", "Quantity", "12", ordinal=2),
        )
    )
    assert [stored.field_name for stored in result.values] == ["quantity"]
    assert [(member.position, member.item_ordinal) for member in result.line_items] == [(0, 2)]
    refusal = next(entry for entry in result.failures if entry.field_name == "manufacturer")
    assert refusal.outcome == OUTCOME_SCHEMA_VIOLATION
    assert "FR-059" in refusal.detail


def test_every_stored_value_has_exactly_one_membership() -> None:
    """SC-046, counted rather than inspected."""
    result, _ = stage(
        values_json(
            value("manufacturer", "Manufacturer", "Nordway", ordinal=1),
            value("quantity", "Quantity", "12", ordinal=1),
            value("submittal_number", "Transmittal No.", "T0001"),
        )
    )
    assert len(result.line_items) == len(result.values) == 3
    assert sorted(member.position for member in result.line_items) == list(range(3))


# ---------------------------------------------------------------------------
# FR-029 — the page-split citation, derived from the chunk text
# ---------------------------------------------------------------------------


def test_a_page_split_value_cites_the_value_page_and_keeps_the_label_chunk() -> None:
    """The anchor is the chunk printing the value; the label's chunk contributes.

    Derived from the chunk text alone — the label is absent from the anchor and
    present on the previous page — with no template and no pre-render model
    consulted.
    """
    chunks = [
        chunk(0, page=1, body="Submittal Descriptor: SD-03\nManufacturer:"),
        chunk(1, page=2, body="Nordway Fabrication\nQty.: 12"),
    ]
    result, _ = stage(
        values_json(),
        values_json(value("manufacturer", "Manufacturer", "Nordway Fabrication")),
        chunks=chunks,
    )
    stored = next(entry for entry in result.values if entry.field_name == "manufacturer")
    assert stored.citation.cited_page == 2
    assert stored.citation.anchor.ordinal == 1
    assert [contributor.ordinal for contributor in stored.citation.contributors] == [0]
    assert stored.citation.source_chunk_count == 2
    assert stored.citation.provenance_kind == "multi_chunk"
    # FR-057's page-split deduction fired, and only it.
    assert stored.confidence == 1.0 - DECLARED_DEDUCTION_PAGE_SPLIT
    assert stored.signals.page_split


def test_a_label_present_in_the_anchor_chunk_produces_no_contributor() -> None:
    """The ordinary case, asserted so the split rule cannot fire on everything."""
    result, _ = stage(values_json(value("manufacturer", "Manufacturer", "Nordway")))
    stored = result.values[0]
    assert stored.citation.contributors == ()
    assert stored.citation.provenance_kind == "single_chunk"


# ---------------------------------------------------------------------------
# FR-056 — the run-level failure leaves untouched, which is the fixture-less case
# ---------------------------------------------------------------------------


def test_a_missing_fixture_aborts_the_stage_as_a_run_level_failure() -> None:
    """T081: zero fixtures are committed, so this is what a `replay` run reaches.

    Asserted as reaching `fixture_missing` **cleanly** — a named run-level kind
    carrying the resolution key — rather than as a crash or as a document full
    of per-field failure rows.
    """
    miss = FixtureMissError("sha256:" + "a" * 64, Path("src/gateway/fixtures"))
    with pytest.raises(ExtractionRunFailure) as raised:
        stage(miss)
    assert raised.value.kind == RUN_FAILURE_FIXTURE_MISSING
    assert "a" * 64 in raised.value.detail


def test_an_unreachable_provider_aborts_the_stage_too_and_under_its_own_kind() -> None:
    with pytest.raises(ExtractionRunFailure) as raised:
        stage(ProviderUnavailableError("the provider SDK is not installed"))
    assert raised.value.kind == "provider_unreachable"


def test_the_run_level_abort_stops_the_document_rather_than_continuing() -> None:
    """A stage that carried on would keep spending against a broken configuration."""
    invoker = ScriptedInvoker(
        FixtureMissError("sha256:" + "b" * 64, Path("src/gateway/fixtures")),
        values_json(value("manufacturer", "Manufacturer", "Nordway")),
    )
    with pytest.raises(ExtractionRunFailure):
        run_extraction_stage(
            document_id=DOCUMENT_ID,
            chunks=[chunk(0), chunk(1, page=2)],
            fields=FIELDS,
            run_id=RUN_ID,
            trace_id=TRACE_ID,
            policy=POLICY,
            invoke=invoker,
        )
    assert len(invoker.seen) == 1, "the second chunk was invoked after a run-level abort"


# ---------------------------------------------------------------------------
# The guards, each refused before the first invocation
# ---------------------------------------------------------------------------


def test_a_document_with_no_chunk_is_refused_before_anything_is_invoked() -> None:
    invoker = ScriptedInvoker()
    with pytest.raises(ExtractionStageError, match="zero chunks"):
        run_extraction_stage(
            document_id=DOCUMENT_ID,
            chunks=[],
            fields=FIELDS,
            run_id=RUN_ID,
            trace_id=TRACE_ID,
            policy=POLICY,
            invoke=invoker,
        )
    assert invoker.seen == []


def test_an_empty_attempted_subset_is_refused() -> None:
    with pytest.raises(ExtractionStageError, match="FR-024"):
        run_extraction_stage(
            document_id=DOCUMENT_ID,
            chunks=[chunk(0)],
            fields=[],
            run_id=RUN_ID,
            trace_id=TRACE_ID,
            policy=POLICY,
            invoke=ScriptedInvoker(),
        )


def test_a_chunk_of_another_document_is_refused() -> None:
    """A citation carries an ordinal, and an ordinal resolves inside a transaction."""
    other = Chunk(
        document_id="prj-002-t0009-r0",
        document_type="transmittal",
        project_id="PRJ-002",
        page_number=1,
        ordinal=0,
        body_text="Manufacturer: Elsewhere",
        boundary_class="structural",
        structural_identifier="p1-body0",
    )
    with pytest.raises(ExtractionStageError, match="prj-002-t0009-r0"):
        run_extraction_stage(
            document_id=DOCUMENT_ID,
            chunks=[chunk(0), other],
            fields=FIELDS,
            run_id=RUN_ID,
            trace_id=TRACE_ID,
            policy=POLICY,
            invoke=ScriptedInvoker(),
        )


# ---------------------------------------------------------------------------
# FR-057's first signal — canonical or alternate, and nothing else
# ---------------------------------------------------------------------------


def test_the_terms_own_label_is_canonical() -> None:
    assert classify_label("Manufacturer", term("manufacturer")) == LABEL_MATCH_CANONICAL
    assert classify_label("  manufacturer  ", term("manufacturer")) == LABEL_MATCH_CANONICAL


def test_a_committed_alternate_label_is_alternate() -> None:
    """`Mfr` is one of the vocabulary's alternates for the manufacturer field."""
    assert classify_label("Mfr", term("manufacturer")) == LABEL_MATCH_ALTERNATE


def test_a_label_the_vocabulary_does_not_know_is_alternate_not_a_third_value() -> None:
    """The column's domain is closed at two, and the deduction is for 'not canonical'."""
    assert classify_label("Made By", term("manufacturer")) == LABEL_MATCH_ALTERNATE


def test_an_alternate_label_deducts_exactly_once_and_the_value_is_still_stored() -> None:
    """0.85 is at or above the declared 0.80 floor, so it is persisted intact."""
    result, _ = stage(values_json(value("manufacturer", "Mfr", "Nordway")))
    stored = result.values[0]
    assert stored.signals.label_match == LABEL_MATCH_ALTERNATE
    assert stored.confidence == 1.0 - DECLARED_DEDUCTION_ALTERNATE_LABEL
    check_confidence_agrees(stored, POLICY)


def test_the_attempted_subset_this_suite_narrows_is_a_subset_of_the_declared_one() -> None:
    """So a term retired from the declaration cannot leave these tests green."""
    declared = {entry.name for entry in TRANSMITTAL_FIELD_SUBSET}
    assert {entry.name for entry in FIELDS} <= declared
