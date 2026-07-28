"""The traced model path — the only module in this repository importing `gateway`.

FR-023, FR-048, AD-001. Every model request this project makes leaves from
`extract_fields` below and from nowhere else. Two mechanisms hold that, and
neither is review: the committed forbidden contract in `src/model/pyproject.toml`
names `model.llm` as its source and refuses any edge from it into
`model.compute`, and `tests/checks/test_model_facing_placement.py` refuses a
`gateway` import from any module under `/src/model` outside `model/llm/`.

**What this module deliberately does not do.** It computes no confidence,
coerces no value to a typed form, and applies no metric. `model.llm` may not
reach `model.compute` — the contract's `allow_indirect_imports = false` means a
laundered path through `model.ingest` fails the build exactly as a direct import
does — so those three are applied by the orchestrator *after* this returns. That
is the shape ADR-0008 and Principle V require: the model extracts, code computes.

**What the gateway fixes, and this module therefore does not re-decide.** The
repair budget is **1**, enforced inside the gateway (TR-007); a second failure
raises `GatewayValidationError`, which carries the failing field paths and never
the model's output — returning the output would hand back the unvalidated value
through the error rather than through the return. `max_tokens` is the gateway's
4096. `InvocationRequest` declares exactly four fields under `extra="forbid"` —
there is no temperature, no system prompt, and no structured-output parameter —
so the JSON contract is prompt text and `output_schema` is what checks it.

**Validate, repair at most once, then fail closed** (FR-025, FR-026). The order
is fixed and there is no branch out of it. `output_schema` is submitted with the
request, so the gateway validates the first response against *the caller's*
schema; a failure spends the single repair; a second failure raises. Nothing
unvalidated is returned, persisted, or logged — this module re-validates the
returned content against the same schema before it constructs an outcome, and no
message it raises interpolates `result.content`. `REPAIR_BUDGET` is imported from
`gateway.validation` rather than written as `1` here, because a budget restated
on this side is a second number that can disagree with the one actually enforced.

**Two failure classes, and the split is FR-056's.** A missing fixture in replay
or an unreachable provider is a **run-level** failure that aborts the run: it is
not one of the seven per-field outcomes, because nothing was ever asked of the
model and no source chunk explains it. A model output that will not validate is a
**per-field** failure. `ExtractionRunFailure` and `ExtractionSchemaViolation` are
those two, kept apart here so a caller cannot record one as the other by catching
too widely.

**FR-026's fail-closed outcome is `repair_budget_exhausted`, and it is not the
same row as `schema_violation`.** Both are members of FR-034's closed seven and
E003's `ck_extraction_failure__outcome` admits both, so collapsing them would
publish a repair budget that was never observed to be spent. The rule is the
gateway's own `repair_attempt_count`: a validation failure reported with the
budget spent is `repair_budget_exhausted`; one reported with nothing spent — a
name outside the vocabulary (FR-024), or content that does not satisfy the
submitted schema at all — is `schema_violation`. `ExtractionSchemaViolation`
carries the outcome and the attempt count, so the caller writes FR-035's fields
from the error rather than deciding them again.

**The trace identifier is explicit.** The gateway reads no thread-local and no
context variable (TR-080), so the run's one trace identifier is a parameter on
every call — which is what makes FR-070's reconciliation possible at all: every
invocation of a run is recorded under an identifier the run itself chose.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from gateway.api import InvocationRequest, InvocationResult
from gateway.api import invoke as gateway_invoke
from gateway.errors import GatewayError, GatewayValidationError
from gateway.fixtures import FixtureMissError
from gateway.validation import MAX_REPAIR_ATTEMPTS
from pydantic import ValidationError

from model.llm.prompts import PROMPT_TEMPLATE_ID, render_extraction_prompt
from model.llm.schemas import ChunkExtraction, ExtractedField, FieldTerm

__all__ = [
    "OUTCOME_REPAIR_BUDGET_EXHAUSTED",
    "OUTCOME_SCHEMA_VIOLATION",
    "PROMPT_TEMPLATE_ID",
    "REPAIR_BUDGET",
    "RUN_FAILURE_FIXTURE_MISSING",
    "RUN_FAILURE_PROVIDER_UNREACHABLE",
    "VALIDATED_OUTCOMES",
    "ExtractionChunk",
    "ExtractionError",
    "ExtractionOutcome",
    "ExtractionRunFailure",
    "ExtractionSchemaViolation",
    "Invoker",
    "extract_fields",
]

#: TR-007's budget, taken from the gateway's own constant rather than written as
#: `1` here. It is **not** configurable from this side: `validate_or_repair`
#: enforces it and raises when it is spent, so a literal on this side would be a
#: second number that can disagree with the one that actually runs — which is
#: exactly the disagreement FR-026's "at most one" would then be measured
#: against.
REPAIR_BUDGET: Final[int] = MAX_REPAIR_ATTEMPTS

#: Two of FR-056's five run-level kinds — the two this module can produce.
#: `corpus_digest_mismatch`, `document_id_collision` and `oversized_sentence`
#: arise before any invocation and belong to the intake path.
RUN_FAILURE_FIXTURE_MISSING: Final[str] = "fixture_missing"
RUN_FAILURE_PROVIDER_UNREACHABLE: Final[str] = "provider_unreachable"

#: FR-025: the only two gateway outcomes that accompany a validated value. The
#: third, `failed`, is never *returned* — TR-006 makes it a raise — so a result
#: carrying it is a gateway that handed back an unvalidated value, and this
#: module refuses it rather than reading its content.
VALIDATED_OUTCOMES: Final[tuple[str, ...]] = ("valid", "repaired")

#: FR-034's members this module can produce, both from `ck_extraction_failure__
#: outcome`'s closed seven. Named constants rather than inline strings so the
#: caller writing the row and the classifier choosing the value are the same
#: two objects.
OUTCOME_SCHEMA_VIOLATION: Final[str] = "schema_violation"
OUTCOME_REPAIR_BUDGET_EXHAUSTED: Final[str] = "repair_budget_exhausted"


class ExtractionError(RuntimeError):
    """Base for both failure classes, so a caller can catch neither by accident.

    Catching this catches both a run-level abort and a per-field refusal, which
    is almost always the wrong thing to do — the two have different homes in the
    database and different consequences for the run. It exists so that
    `except ExtractionError` is a deliberate choice rather than the only choice.
    """


class ExtractionRunFailure(ExtractionError):
    """FR-056: the run cannot continue, and no per-field row can explain it.

    A missing fixture in `replay` mode is the canonical case. Nothing was asked
    of the model, so there is no attempt to record an outcome for; and the row
    that would carry a per-field failure cannot be written either, because
    `extraction_failure.source_chunk_id` is NOT NULL against a chunk the
    document's rollback has just removed. It is recorded on `ingestion_run` as
    `run_failure_kind` plus `run_failure_detail`, in a fresh transaction after
    the rollback (`data-model.md` §Write Order).

    `kind` is one of the five `ck_ingestion_run__failure_kind_domain` admits, so
    a caller writes the column rather than mapping an exception type to a string
    of its own devising.
    """

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


class ExtractionSchemaViolation(ExtractionError):
    """FR-025 / FR-026: no schema-valid output, with or without the one repair.

    Per-field, and recorded as an `extraction_failure` row. Carries the failing
    field paths the gateway reported and **never the model's output** — the
    output is what failed validation, so passing it along would be handing back
    the unvalidated value FR-025 forbids, arriving by a quieter route.

    `outcome` is one of FR-034's seven and is decided from `repair_attempt_count`
    rather than from the call site: `repair_budget_exhausted` when the single
    repair was spent and the second response failed too, `schema_violation` when
    the output was refused without a repair having been available or attempted.
    `repair_attempt_count` is FR-035's fourth required field and is carried here
    so the caller writes the row from the error rather than counting again.
    """

    def __init__(
        self,
        detail: str,
        *,
        field_paths: tuple[str, ...] = (),
        repair_attempt_count: int = 0,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.field_paths = field_paths
        self.repair_attempt_count = repair_attempt_count

    @property
    def outcome(self) -> str:
        """The `extraction_failure.outcome` this refusal is recorded under."""
        if self.repair_attempt_count >= REPAIR_BUDGET:
            return OUTCOME_REPAIR_BUDGET_EXHAUSTED
        return OUTCOME_SCHEMA_VIOLATION


@dataclass(frozen=True)
class ExtractionChunk:
    """The one chunk an invocation covers (FR-069's invocation unit).

    One invocation per chunk, covering that chunk's declared field subset. The
    citation fields travel with it because the values that come back inherit
    their page from this chunk and from nothing else (FR-029) — a caller that
    had to remember which chunk a result belonged to could get it wrong, and the
    error would be an unattributable citation rather than an exception.
    """

    document_id: str
    chunk_id: str
    ordinal: int
    page_number: int
    body_text: str


@dataclass(frozen=True)
class ExtractionOutcome:
    """What one invocation produced, with what the record will say about it.

    `repaired` is read from the gateway's own outcome rather than counted here.
    It is FR-057's third deduction signal and FR-063's `validated_after_repair`
    column, and taking it from the invocation result is what keeps the stored
    signal equal to what the invocation record says — two counters would be two
    answers.
    """

    chunk: ExtractionChunk
    values: tuple[ExtractedField, ...]
    invocation_id: str
    trace_id: str
    outcome: str
    resolution_mode: str

    @property
    def repaired(self) -> bool:
        return self.outcome == "repaired"


#: The gateway entry point, as a type a caller may substitute. Substitution is
#: for tests only — the default *is* the single traced path, and a caller that
#: passed something else would be making a model request outside it, which
#: FR-023 forbids. Exposed as a parameter rather than patched at module scope
#: because a monkeypatched global is invisible at the call site.
type Invoker = Callable[[InvocationRequest], InvocationResult]


def _classify(error: GatewayError) -> ExtractionError:
    """Map a gateway failure onto FR-056's run level or FR-025's field level.

    A fixture miss is named explicitly rather than folded into a generic
    "gateway failed", because the two have different fixes: a miss means the
    prompt or the schema moved and the fixtures must be re-recorded (FR-045),
    while an unreachable provider means the run cannot proceed at all. Both
    abort; only one of them is repaired by running `record` mode.

    Everything that is not a validation failure is treated as run-level. That
    direction is chosen deliberately: mis-classifying a run-level failure as
    per-field would write a `schema_violation` row for a call that never
    happened and let the run continue past a broken configuration, while
    mis-classifying the other way aborts a run that might have limped on. Under
    Principle III the loud failure is the correct default.
    """
    if isinstance(error, GatewayValidationError):
        return ExtractionSchemaViolation(
            f"no schema-valid output within the repair budget of {REPAIR_BUDGET} (FR-026): {error}",
            field_paths=error.field_paths,
            repair_attempt_count=error.repair_attempt_count,
        )
    if isinstance(error, FixtureMissError):
        return ExtractionRunFailure(
            RUN_FAILURE_FIXTURE_MISSING,
            f"{error}. FR-045 requires fixtures to be re-recorded whenever the prompt "
            f"text or an output schema constraint changes; the key covers both.",
        )
    return ExtractionRunFailure(
        RUN_FAILURE_PROVIDER_UNREACHABLE,
        f"{type(error).__name__}: {error}",
    )


def extract_fields(
    chunk: ExtractionChunk,
    fields: Sequence[FieldTerm],
    *,
    trace_id: str,
    model: str | None = None,
    invoke: Invoker | None = None,
) -> ExtractionOutcome:
    """Issue one traced invocation for one chunk, and return what it read.

    Args:
        chunk: the chunk under review. One invocation covers exactly one chunk,
            which is FR-069's invocation unit and the denominator SC-018's
            valid, repaired and failed counts are published against.
        fields: the declared, unretired transmittal subset (FR-024, FR-058).
            Rendered into the prompt, which is a hashed request field — so a
            narrowed subset resolves a different fixture key rather than
            replaying one recorded for a wider one.
        trace_id: the run's one identifier, recorded on `ingestion_run.
            run_trace_id` and on every invocation row (FR-070). Explicit rather
            than ambient, because the gateway reads no context variable and
            because the reconciliation in the report is only checkable if the
            identifier was chosen by the run rather than minted per call.
        model: the provider model, or `None` for the gateway's default. Recorded
            on the run record either way.
        invoke: the gateway entry point. Defaults to the single traced path;
            supplied only by tests, which is why it is a parameter rather than
            a patchable global.

    Returns:
        The values the model reported, exactly as printed, with the invocation
        and trace identifiers so the caller can join what it received to the row
        the gateway wrote.

    Raises:
        ExtractionRunFailure: a missing fixture in `replay`, or a provider that
            could not be reached. FR-056: the run aborts and the failure is
            recorded on the run record, never as a per-field row.
        ExtractionSchemaViolation: the model produced nothing schema-valid.
            Per-field, and the error's own `outcome` names which of FR-034's
            seven the row takes — `repair_budget_exhausted` when the single
            repair was spent, `schema_violation` when the output was refused
            without one.
        ValueError: the chunk carries no text, or the field subset is empty.
            Refused before the request is built, so it costs no invocation.
    """
    prompt = render_extraction_prompt(
        document_id=chunk.document_id,
        page_number=chunk.page_number,
        chunk_ordinal=chunk.ordinal,
        chunk_text=chunk.body_text,
        fields=fields,
    )
    request = InvocationRequest(
        prompt=prompt,
        model=model,
        trace_id=trace_id,
        output_schema=ChunkExtraction,
    )

    call = gateway_invoke if invoke is None else invoke
    try:
        result = call(request)
    except GatewayError as error:
        raise _classify(error) from None

    if result.outcome not in VALIDATED_OUTCOMES:
        # FR-025's prohibition read from the other side: a returned result whose
        # outcome is not one of the two that accompany a validated value is an
        # unvalidated value arriving through the return. TR-006 makes this
        # unreachable through a conforming gateway; it is refused rather than
        # asserted so one malformed result costs one chunk instead of the run,
        # and the content is neither read nor named in the message.
        raise ExtractionSchemaViolation(
            f"the gateway returned outcome {result.outcome!r} for "
            f"{chunk.document_id} ordinal {chunk.ordinal}, which is outside "
            f"{list(VALIDATED_OUTCOMES)}. Only a validated value is returned "
            f"(TR-006); an unvalidated one is not persisted, returned, or logged.",
            field_paths=(),
        )

    try:
        extraction = ChunkExtraction.model_validate_json(result.content)
    except ValidationError as error:
        # Reachable only if the gateway returned content that does not satisfy
        # the schema it was handed — which TR-006 forbids. Treated as a
        # per-field schema violation rather than an assertion, because the
        # alternative is an unhandled exception aborting a run over one chunk.
        raise ExtractionSchemaViolation(
            f"the gateway returned content that does not satisfy the submitted output "
            f"schema for {chunk.document_id} ordinal {chunk.ordinal}: "
            f"{error.error_count()} validation error(s)",
            field_paths=tuple(
                ".".join(str(part) for part in item["loc"]) for item in error.errors()
            ),
        ) from None

    return ExtractionOutcome(
        chunk=chunk,
        values=tuple(extraction.values),
        invocation_id=result.invocation_id,
        trace_id=result.trace_id,
        outcome=result.outcome,
        resolution_mode=result.resolution_mode,
    )
