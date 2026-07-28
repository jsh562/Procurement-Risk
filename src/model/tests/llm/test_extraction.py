"""FR-023: the traced invocation path, and the schemas and prompts it imports.

T092. Three things are checked here that nothing else in the repository checks:

1. **The invocation path itself** — that `extract_fields` builds a request the
   gateway's closed four-field model accepts, submits the output schema, passes
   the run's trace identifier through unchanged, and returns what the model
   read.
2. **The two failure classes stay apart** (FR-056). A missing fixture is a
   run-level abort; a model output that will not validate is a per-field
   `schema_violation`. A test that caught `ExtractionError` for both would pass
   while the distinction was lost, so each is asserted by its own type.
3. **The vocabulary transcription is not drift.** `schemas.SEEDED_VOCABULARY`
   restates revision `0005`'s 22 terms, and a restatement nobody compares is a
   second definition. This file parses the revision's own `INSERT` and compares
   names, kinds, and order.

The gateway is reached through an **injected invoker** rather than a
monkeypatched module global. The default `invoke` opens a database connection,
resolves a price pin, and drains a spool before it reaches the mode branch —
none of which this file is about — and a patched global would hide at the top of
the module which call was substituted.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from gateway.api import InvocationRequest, InvocationResult
from gateway.compute.hashing import fixture_key
from gateway.errors import (
    GatewayConfigError,
    GatewayValidationError,
    ProviderUnavailableError,
)
from gateway.fixtures import FixtureMissError

from model.llm import prompts, schemas
from model.llm.extraction import (
    RUN_FAILURE_FIXTURE_MISSING,
    RUN_FAILURE_PROVIDER_UNREACHABLE,
    ExtractionChunk,
    ExtractionRunFailure,
    ExtractionSchemaViolation,
    extract_fields,
)

#: `.../src/model/tests/llm/test_extraction.py` — three levels up is `src/model`,
#: the modeling entry's own root. Resolved from `__file__` rather than from a
#: working directory, because this file reads a migration source and walks the
#: package directory and both must work from any cwd pytest is started in.
ENTRY_ROOT = Path(__file__).resolve().parents[2]
MODEL_PACKAGE = ENTRY_ROOT / "src" / "model"
VOCABULARY_REVISION = MODEL_PACKAGE / "schema" / "versions" / "0005_field_vocabulary.py"

TRACE_ID = "0123456789abcdef0123456789abcdef"


CHUNK = ExtractionChunk(
    document_id="prj-001-t0001-r0",
    chunk_id="1b4e28ba-2fa1-11d2-883f-0016d3cca427",
    ordinal=0,
    page_number=1,
    body_text="Manufacturer: Nordway Fabrication\nPart No.: NRD-000417\nQuantity: 12",
)


# ---------------------------------------------------------------------------
# A recording invoker, so every assertion about the request is on a real one
# ---------------------------------------------------------------------------


@dataclass
class RecordingInvoker:
    """Captures the request and answers with a canned result or a failure."""

    content: str = '{"values": []}'
    outcome: str = "valid"
    resolution_mode: str = "replay"
    raises: Exception | None = None
    seen: list[InvocationRequest] | None = None

    def __post_init__(self) -> None:
        self.seen = []

    def __call__(self, request: InvocationRequest) -> InvocationResult:
        assert self.seen is not None
        self.seen.append(request)
        if self.raises is not None:
            raise self.raises
        return InvocationResult(
            invocation_id="a3f1c0de-0000-4000-8000-000000000001",
            trace_id=request.trace_id or TRACE_ID,
            content=self.content,
            outcome=self.outcome,
            resolution_mode=self.resolution_mode,
        )


def _values_json(*entries: str) -> str:
    return '{"values": [' + ", ".join(entries) + "]}"


MANUFACTURER_VALUE = (
    '{"field_name": "manufacturer", "printed_label": "Manufacturer", '
    '"value_text": "Nordway Fabrication", "item_ordinal": 1}'
)
QUANTITY_VALUE = (
    '{"field_name": "quantity", "printed_label": "Qty.", "value_text": "12", "item_ordinal": 1}'
)


# ---------------------------------------------------------------------------
# FR-024 — the vocabulary bound
# ---------------------------------------------------------------------------


def seeded_terms_from_revision() -> tuple[tuple[str, str], ...]:
    """`(field_name, value_kind)` in the order revision `0005` seeds them.

    Read from the revision's source rather than from a live database: the
    comparison is between two *declarations*, and requiring a migrated database
    to make it would leave the transcription unchecked on every run that has no
    server — which is most of them.
    """
    source = VOCABULARY_REVISION.read_text(encoding="utf-8")
    insert = source.split("INSERT INTO field_vocabulary", 1)[1]
    return tuple(
        (name, kind)
        for name, kind in re.findall(r"\(\s*'([a-z_]+)',\s*'(text|number|date)'", insert)
    )


def test_the_transcribed_vocabulary_is_the_seeded_one() -> None:
    """FR-024. The restatement is compared, so it cannot quietly drift.

    Names *and* kinds *and* order. Kind matters because it decides which of
    `extracted_value`'s two value columns is populated; order matters only so a
    reader can diff the two lists line for line, which is what makes a
    discrepancy cheap to locate.
    """
    transcribed = tuple((entry.name, entry.value_kind) for entry in schemas.SEEDED_VOCABULARY)
    assert transcribed == seeded_terms_from_revision()
    assert len(transcribed) == 22


def test_every_seeded_term_is_attempted_or_excluded_with_a_reason() -> None:
    """FR-058. A term in neither list is unattempted and unreported at once."""
    attempted = {entry.name for entry in schemas.TRANSMITTAL_FIELD_SUBSET}
    excluded = set(schemas.EXCLUDED_TERMS)
    assert attempted | excluded == {entry.name for entry in schemas.SEEDED_VOCABULARY}
    assert not attempted & excluded
    assert all(reason.strip() for reason in schemas.EXCLUDED_TERMS.values())


def test_the_subset_is_smaller_than_the_whole_vocabulary() -> None:
    """AD-009's whole point: attempting all 22 per chunk buys impossible calls."""
    assert len(schemas.TRANSMITTAL_FIELD_SUBSET) < len(schemas.SEEDED_VOCABULARY)
    assert len(schemas.EXCLUDED_TERMS) >= 10


def test_a_name_outside_the_vocabulary_is_refused() -> None:
    """FR-024: the vocabulary is not widened at run time."""
    with pytest.raises(schemas.SchemaError, match="seeded vocabulary"):
        schemas.term("shipping_container_number")


def test_attempted_terms_drops_a_retired_term() -> None:
    """FR-024's unretired filter, which is this epic's obligation (E003 G-7)."""
    every = {entry.name for entry in schemas.SEEDED_VOCABULARY}
    without_quantity = every - {"quantity"}
    attempted = schemas.attempted_terms(without_quantity)
    assert "quantity" not in {entry.name for entry in attempted}
    assert len(attempted) == len(schemas.TRANSMITTAL_FIELD_SUBSET) - 1


def test_attempted_terms_refuses_an_empty_vocabulary() -> None:
    """An empty set attempts nothing and reports a clean run. It is a failure."""
    with pytest.raises(schemas.SchemaError, match="zero unretired"):
        schemas.attempted_terms(())


def test_attempted_terms_refuses_an_unknown_offered_term() -> None:
    with pytest.raises(schemas.SchemaError, match="not among"):
        schemas.attempted_terms({"manufacturer", "invented_term"})


def test_bound_field_names_splits_rather_than_raising() -> None:
    """FR-024 routes a refusal to a per-field `schema_violation` row.

    So the caller needs *every* refused name, not the first — and needs the
    accepted ones kept, so one bad name does not cost the chunk.
    """
    accepted, refused = schemas.bound_field_names(
        ["manufacturer", "unit_price", "manufacturer", "invented"],
        schemas.TRANSMITTAL_FIELD_SUBSET,
    )
    assert accepted == ("manufacturer", "manufacturer")
    assert refused == ("unit_price", "invented")


def test_printed_but_unattempted_reports_a_term_outside_the_subset() -> None:
    """FR-058: published as unattempted-but-printed, not absorbed into misses."""
    assert schemas.printed_but_unattempted({"manufacturer", "unit_price"}) == ("unit_price",)
    assert schemas.printed_but_unattempted({"manufacturer"}) == ()


def test_document_scoped_terms_are_declared_not_inferred() -> None:
    """FR-059: ordinal 0 is a named group, so its members are declared."""
    scoped = {
        entry.name
        for entry in schemas.TRANSMITTAL_FIELD_SUBSET
        if entry.scope == schemas.DOCUMENT_SCOPE
    }
    assert {"submittal_number", "submittal_date", "approval_date"} <= scoped
    assert "manufacturer" not in scoped


# ---------------------------------------------------------------------------
# FR-058 — the prompt
# ---------------------------------------------------------------------------


def test_the_prompt_names_every_attempted_field_and_no_other() -> None:
    rendered = prompts.render_extraction_prompt(
        document_id=CHUNK.document_id,
        page_number=CHUNK.page_number,
        chunk_ordinal=CHUNK.ordinal,
        chunk_text=CHUNK.body_text,
        fields=schemas.TRANSMITTAL_FIELD_SUBSET,
    )
    for entry in schemas.TRANSMITTAL_FIELD_SUBSET:
        assert f"`{entry.name}`" in rendered
    for name in schemas.EXCLUDED_TERMS:
        assert f"`{name}`" not in rendered


def test_the_prompt_carries_the_chunk_text_verbatim() -> None:
    rendered = prompts.render_extraction_prompt(
        document_id=CHUNK.document_id,
        page_number=CHUNK.page_number,
        chunk_ordinal=CHUNK.ordinal,
        chunk_text=CHUNK.body_text,
        fields=schemas.TRANSMITTAL_FIELD_SUBSET,
    )
    assert CHUNK.body_text in rendered
    assert CHUNK.document_id in rendered


def test_the_prompt_states_the_json_contract() -> None:
    """The contract is prompt text because `InvocationRequest` has nowhere else.

    Four fields under `extra="forbid"` and no structured-output parameter, so a
    prompt that did not state the shape would leave the model guessing at what
    `output_schema` will then refuse.
    """
    rendered = prompts.render_extraction_prompt(
        document_id=CHUNK.document_id,
        page_number=CHUNK.page_number,
        chunk_ordinal=CHUNK.ordinal,
        chunk_text=CHUNK.body_text,
        fields=schemas.TRANSMITTAL_FIELD_SUBSET,
    )
    for key in ("field_name", "printed_label", "value_text", "item_ordinal"):
        assert f'"{key}"' in rendered


def test_a_narrowed_subset_resolves_a_different_fixture_key() -> None:
    """FR-045, and the reason the output schema is static.

    The attempted subset reaches the fixture key through the prompt, which is a
    hashed request field. A run that retires a term therefore misses in `replay`
    and takes FR-056's run-level failure rather than replaying a fixture
    recorded for a wider subset.
    """

    def key_for(fields: tuple[schemas.FieldTerm, ...]) -> str:
        request = InvocationRequest(
            prompt=prompts.render_extraction_prompt(
                document_id=CHUNK.document_id,
                page_number=CHUNK.page_number,
                chunk_ordinal=CHUNK.ordinal,
                chunk_text=CHUNK.body_text,
                fields=fields,
            ),
            trace_id=TRACE_ID,
            output_schema=schemas.ChunkExtraction,
        )
        return fixture_key(request, schema=schemas.ChunkExtraction)

    whole = key_for(schemas.TRANSMITTAL_FIELD_SUBSET)
    narrowed = key_for(schemas.TRANSMITTAL_FIELD_SUBSET[:-1])
    assert whole != narrowed
    assert whole == key_for(schemas.TRANSMITTAL_FIELD_SUBSET)


def test_an_empty_field_catalogue_is_refused() -> None:
    with pytest.raises(ValueError, match="FR-058"):
        prompts.field_catalogue(())


def test_a_blank_chunk_is_refused_before_any_invocation() -> None:
    """Refused before the request is built, so it costs no invocation and no row."""
    invoker = RecordingInvoker()
    with pytest.raises(ValueError, match="no text"):
        extract_fields(
            ExtractionChunk(
                document_id=CHUNK.document_id,
                chunk_id=CHUNK.chunk_id,
                ordinal=0,
                page_number=1,
                body_text="   \n  ",
            ),
            schemas.TRANSMITTAL_FIELD_SUBSET,
            trace_id=TRACE_ID,
            invoke=invoker,
        )
    assert invoker.seen == []


def test_a_zero_page_number_is_refused() -> None:
    with pytest.raises(ValueError, match="one-based"):
        prompts.render_extraction_prompt(
            document_id=CHUNK.document_id,
            page_number=0,
            chunk_ordinal=0,
            chunk_text="text",
            fields=schemas.TRANSMITTAL_FIELD_SUBSET,
        )


# ---------------------------------------------------------------------------
# FR-023 — the invocation path
# ---------------------------------------------------------------------------


def test_the_request_carries_the_schema_the_trace_id_and_nothing_else() -> None:
    """The gateway's request model declares exactly four fields, `extra="forbid"`.

    Asserted against the model's own field set rather than a list written here,
    so a field added to the gateway fails this test instead of passing it
    unnoticed.
    """
    invoker = RecordingInvoker()
    extract_fields(
        CHUNK,
        schemas.TRANSMITTAL_FIELD_SUBSET,
        trace_id=TRACE_ID,
        model="claude-test-model",
        invoke=invoker,
    )
    assert invoker.seen is not None
    (request,) = invoker.seen
    assert set(InvocationRequest.model_fields) == {
        "prompt",
        "model",
        "trace_id",
        "output_schema",
    }
    assert request.trace_id == TRACE_ID
    assert request.model == "claude-test-model"
    assert request.output_schema is schemas.ChunkExtraction
    assert CHUNK.body_text in request.prompt


def test_the_run_trace_id_is_passed_through_unchanged() -> None:
    """FR-070: every invocation of a run is recorded under the run's identifier.

    Explicit, never ambient — the gateway reads no context variable (TR-080), so
    a run-scoped identifier only exists if the caller supplies the same one on
    every call. The reconciliation in the report is what this makes checkable.
    """
    invoker = RecordingInvoker()
    outcome = extract_fields(
        CHUNK, schemas.TRANSMITTAL_FIELD_SUBSET, trace_id=TRACE_ID, invoke=invoker
    )
    assert outcome.trace_id == TRACE_ID
    assert invoker.seen is not None
    assert invoker.seen[0].trace_id == TRACE_ID


def test_the_reported_values_come_back_as_printed() -> None:
    """FR-027: no normalization on the way out of the model path."""
    invoker = RecordingInvoker(content=_values_json(MANUFACTURER_VALUE, QUANTITY_VALUE))
    outcome = extract_fields(
        CHUNK, schemas.TRANSMITTAL_FIELD_SUBSET, trace_id=TRACE_ID, invoke=invoker
    )
    assert [entry.field_name for entry in outcome.values] == ["manufacturer", "quantity"]
    assert outcome.values[0].value_text == "Nordway Fabrication"
    assert outcome.values[1].printed_label == "Qty."
    assert outcome.values[0].item_ordinal == 1
    assert outcome.chunk is CHUNK
    assert outcome.resolution_mode == "replay"


def test_an_empty_answer_is_not_a_failure() -> None:
    """A chunk printing none of the attempted fields is a correct answer.

    FR-037 records that absence once per *document*, not once per chunk, so this
    path must return cleanly rather than raise.
    """
    outcome = extract_fields(
        CHUNK, schemas.TRANSMITTAL_FIELD_SUBSET, trace_id=TRACE_ID, invoke=RecordingInvoker()
    )
    assert outcome.values == ()
    assert not outcome.repaired


def test_a_repaired_invocation_is_reported_as_repaired() -> None:
    """FR-057's third deduction signal, read from the gateway's own outcome."""
    invoker = RecordingInvoker(content=_values_json(MANUFACTURER_VALUE), outcome="repaired")
    outcome = extract_fields(
        CHUNK, schemas.TRANSMITTAL_FIELD_SUBSET, trace_id=TRACE_ID, invoke=invoker
    )
    assert outcome.repaired
    assert outcome.outcome == "repaired"


# ---------------------------------------------------------------------------
# FR-056 / FR-026 — the two failure classes, kept apart
# ---------------------------------------------------------------------------


def test_a_missing_fixture_is_a_run_level_failure(tmp_path: Path) -> None:
    """FR-056: not one of the seven per-field outcomes, and it aborts the run."""
    miss = FixtureMissError("sha256:" + "0" * 64, tmp_path)
    invoker = RecordingInvoker(raises=miss)
    with pytest.raises(ExtractionRunFailure) as caught:
        extract_fields(CHUNK, schemas.TRANSMITTAL_FIELD_SUBSET, trace_id=TRACE_ID, invoke=invoker)
    assert caught.value.kind == RUN_FAILURE_FIXTURE_MISSING
    assert "re-recorded" in caught.value.detail


def test_an_unreachable_provider_is_a_run_level_failure() -> None:
    invoker = RecordingInvoker(raises=ProviderUnavailableError("the provider extra is absent"))
    with pytest.raises(ExtractionRunFailure) as caught:
        extract_fields(CHUNK, schemas.TRANSMITTAL_FIELD_SUBSET, trace_id=TRACE_ID, invoke=invoker)
    assert caught.value.kind == RUN_FAILURE_PROVIDER_UNREACHABLE


def test_a_configuration_failure_is_run_level_rather_than_per_field() -> None:
    """Mis-classifying this as per-field would write a `schema_violation` row for
    a call that never happened, and let the run continue past a broken
    configuration. Principle III makes the loud failure the default."""
    invoker = RecordingInvoker(raises=GatewayConfigError("GATEWAY_MODE is not set"))
    with pytest.raises(ExtractionRunFailure):
        extract_fields(CHUNK, schemas.TRANSMITTAL_FIELD_SUBSET, trace_id=TRACE_ID, invoke=invoker)


def test_an_exhausted_repair_budget_is_a_per_field_schema_violation() -> None:
    """FR-026: budget 1, then fail closed — and the failure is per field."""
    invoker = RecordingInvoker(
        raises=GatewayValidationError(
            "output failed validation after one repair",
            field_paths=("values.0.value_text",),
            repair_attempt_count=1,
        )
    )
    with pytest.raises(ExtractionSchemaViolation) as caught:
        extract_fields(CHUNK, schemas.TRANSMITTAL_FIELD_SUBSET, trace_id=TRACE_ID, invoke=invoker)
    assert caught.value.field_paths == ("values.0.value_text",)
    assert not isinstance(caught.value, ExtractionRunFailure)


def test_the_violation_carries_field_paths_and_not_the_model_output() -> None:
    """Returning the output would hand back the unvalidated value by a quieter route."""
    withheld_output = "THE MODEL'S UNVALIDATED OUTPUT"
    invoker = RecordingInvoker(
        raises=GatewayValidationError(
            "output failed validation after one repair",
            field_paths=("values.0.field_name",),
        )
    )
    with pytest.raises(ExtractionSchemaViolation) as caught:
        extract_fields(CHUNK, schemas.TRANSMITTAL_FIELD_SUBSET, trace_id=TRACE_ID, invoke=invoker)
    assert withheld_output not in str(caught.value)
    assert caught.value.field_paths


def test_content_that_does_not_satisfy_the_submitted_schema_is_refused() -> None:
    """TR-006 forbids this, so it is unreachable through a conforming gateway.

    Handled anyway, and as a per-field violation rather than an unhandled
    exception: the alternative is one malformed chunk aborting a whole run.
    """
    invoker = RecordingInvoker(content='{"values": [{"field_name": "manufacturer"}]}')
    with pytest.raises(ExtractionSchemaViolation) as caught:
        extract_fields(CHUNK, schemas.TRANSMITTAL_FIELD_SUBSET, trace_id=TRACE_ID, invoke=invoker)
    assert caught.value.field_paths


def test_an_unlisted_key_in_the_answer_is_refused() -> None:
    """`extra="forbid"` is what makes the prompt's contract enforceable."""
    invoker = RecordingInvoker(
        content=(
            '{"values": [{"field_name": "manufacturer", "printed_label": "Manufacturer", '
            '"value_text": "Nordway", "item_ordinal": 1, "confidence": 0.9}]}'
        )
    )
    with pytest.raises(ExtractionSchemaViolation):
        extract_fields(CHUNK, schemas.TRANSMITTAL_FIELD_SUBSET, trace_id=TRACE_ID, invoke=invoker)


def test_the_output_schema_carries_no_confidence_and_no_coerced_form() -> None:
    """Principle V, as a property of the type rather than of a review comment.

    A model that reported its own typed value would be doing FR-049's
    deterministic coercion; one that reported its own confidence would be
    reporting a number nothing could recompute (FR-031). Neither field exists.
    """
    fields = set(schemas.ExtractedField.model_fields)
    assert fields == {"field_name", "printed_label", "value_text", "item_ordinal"}


# ---------------------------------------------------------------------------
# FR-023 / FR-048 — placement, asserted from this side too
# ---------------------------------------------------------------------------


def test_extraction_is_the_only_module_in_model_llm_importing_the_gateway() -> None:
    """`tests/checks/test_model_facing_placement.py` holds the repository-wide
    rule; this holds the narrower one the package can state about itself, so a
    second gateway caller landing beside this module is visible from within the
    package that owns the boundary."""
    package = MODEL_PACKAGE / "llm"
    importers = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name.split(".")[0] == "gateway" for alias in node.names
            ):
                importers.append(path.name)
            if (
                isinstance(node, ast.ImportFrom)
                and not node.level
                and (node.module or "").split(".")[0] == "gateway"
            ):
                importers.append(path.name)
    assert set(importers) == {"extraction.py"}
