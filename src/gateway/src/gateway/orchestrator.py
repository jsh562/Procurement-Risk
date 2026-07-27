"""Composes the call, the validation, the computation and the record.

TR-032. This module exists so that `gateway.provider` never has to reach
`gateway.compute`. The computation-boundary contract forbids that edge with
indirect detection on, and a boundary with nothing above it would have no way
to satisfy the rule except by putting the arithmetic where it must not go —
so the seam is the enforcement, not decoration around it.

Phase 2 lands the composition and the pieces OBJ1 owns. Validation with bounded
repair (Phase 3), the pure arithmetic (Phase 4), fixture resolution (Phase 5)
and the record write (Phase 4) attach at the points marked below. Each is a
named step rather than an inline call so the later phase extends a seam instead
of restructuring this function.

TR-075: the invocation record is the only telemetry this epic emits. Nothing
here starts a span, increments a metric, or installs an exporter or a context
propagator, and the package takes no dependency on an OpenTelemetry SDK. The
field *names* follow the OpenTelemetry generative-AI conventions so the record
stays exportable later (TR-013), which is a naming choice and not an
integration.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, NoReturn, Protocol

from pydantic import BaseModel

from gateway import provider
from gateway.compute.hashing import fixture_key, repair_fixture_key
from gateway.compute.pricing import (
    CostOutOfRangeError,
    TokenCounts,
    compute_cost,
    resolve_price_entry,
)
from gateway.compute.timing import AttemptUsage, elapsed_ms
from gateway.config import (
    PRICE_TABLE_PIN_ENV_VAR,
    RECORD_MODE,
    GatewayConfig,
    load_config,
    require_no_credential_in_replay,
    require_provider_opt_in,
    resolve_mode,
)
from gateway.errors import GatewayConfigError, GatewayError, GatewayValidationError
from gateway.fixtures import (
    FIXTURE_LOOKUP_ATTEMPTS,
    FixtureProvenance,
    FixtureStore,
)
from gateway.models import (
    InvocationRecord,
    InvocationRequest,
    InvocationResult,
    Outcome,
    generate_trace_id,
    is_valid_trace_id,
)
from gateway.record.reconcile import drain_spool
from gateway.record.spool import InvocationSpool
from gateway.record.writer import (
    RecordWriteError,
    RecordWriter,
    log_invocation_complete,
    logger,
)
from gateway.validation import MAX_REPAIR_ATTEMPTS, validate_or_repair

__all__ = [
    "Attempted",
    "PinResolver",
    "RecordSink",
    "RecordWriter",
    "Resolution",
    "classify_outcome",
    "invoke",
    "record_then_raise",
    "require_resolvable_price_pin",
    "resolve_trace_id",
]

#: Recorded on every row as the provider's convention-named identifier
#: (TR-012). A constant rather than a literal at the call site so the value the
#: rows carry is greppable, and spelled without naming the distribution — the
#: single-naming-site scan holds that count at one.
PROVIDER_NAME: Final[str] = "claude"

#: The convention's operation enumeration (TR-013). Deliberately not
#: CHECK-constrained on the column: that value set moves with the pin, and
#: pinning it in DDL would make a pin bump a migration.
OPERATION_NAME: Final[str] = "chat"

DEFAULT_MAX_TOKENS: Final[int] = 4096

#: Where committed fixtures live, relative to the entry root.
DEFAULT_FIXTURE_ROOT: Final[Path] = Path(__file__).resolve().parents[2] / "fixtures"


def _utc_now() -> datetime:
    """The wall clock, injectable through `Resolution` so a test can fix it.

    Separate from `time.monotonic`: this one stamps the row and must be a real
    date, while the monotonic clock measures duration and must not jump.
    """
    return datetime.now(UTC)


def resolve_trace_id(request: InvocationRequest) -> str:
    """The identifier this invocation will be recorded under.

    TR-031: a caller-supplied identifier is used as given, and one is generated
    when the caller supplies none, so a record is never untraceable to the
    request that caused it.

    The revalidation here is not redundant with `InvocationRequest`'s field
    validator. That validator guards the *construction* path; this guards the
    *use* path, and they are reachable independently — `model_construct`
    bypasses validation entirely, and a later phase may rebuild a request from
    a stored payload. TR-047 places the domain check at the boundary before
    use, and this is that boundary.
    """
    supplied = request.trace_id
    if supplied is None:
        return generate_trace_id()
    if not is_valid_trace_id(supplied):
        raise ValueError("trace_id must be 32 lowercase hexadecimal characters and not all zero")
    return supplied


def invoke(request: InvocationRequest) -> InvocationResult:
    """Run one invocation end to end.

    "One invocation" is the unit throughout: exactly one record per invocation,
    never one per attempt (TR-011), with token counts summed and latency
    measured across every attempt it contains (TR-040).

    The steps below are ordered as they are because each later phase's failure
    mode depends on the earlier one having happened. Resolving the trace
    identifier first means even the earliest failure has something to be
    recorded under.
    """
    return _invoke(request, Resolution.from_environment())


def classify_outcome(*, reached_valid_value: bool, repair_attempt_count: int) -> Outcome:
    """The invocation's single outcome value (TR-009, TR-042, TR-078).

    **Total by construction.** TR-078 requires the mapping be total and
    exhaustive rather than stated as the negative rule "a transport failure is
    never `repaired`". Every reachable combination of the two inputs lands on
    exactly one of the three values, and the combination that the negative rule
    alone leaves unclassified — an invocation that consumed transport retries
    *and then* repaired successfully — is `repaired` here, because the repair is
    what produced the returned value.

    **The transport attempt count is not a parameter, and its absence is the
    point.** TR-078 says the classification holds "whatever the transport
    attempt count", so taking one would offer a knob that must never be turned.
    A transport failure cannot be `repaired` because it reaches no valid value
    at all, which makes `reached_valid_value` false — the rule falls out of the
    inputs rather than being enforced by a guard someone could later drop.

    **The unit is the invocation** (TR-042). One value per invocation, never one
    per attempt; nothing here returns a per-attempt classification and no caller
    can assemble one, because the attempt counts it would need are not inputs.

    Args:
        reached_valid_value: Whether a schema-valid value was obtained at all.
            False covers all three failure causes TR-078 lists — repair budget
            exhausted, transport budget exhausted, deadline expired — which are
            distinguished on the record by `error_type` (TR-064), not here.
        repair_attempt_count: 0 or 1. TR-007 bounds the repair budget at one, so
            a higher value means a caller has miscounted and is rejected rather
            than silently classified.

    Returns:
        `valid`, `repaired`, or `failed`.

    Raises:
        ValueError: `repair_attempt_count` is outside the budget TR-007 fixes.
    """
    if not 0 <= repair_attempt_count <= MAX_REPAIR_ATTEMPTS:
        raise ValueError(
            f"repair_attempt_count must be between 0 and {MAX_REPAIR_ATTEMPTS} "
            f"(TR-007); got {repair_attempt_count}"
        )
    if not reached_valid_value:
        return "failed"
    return "repaired" if repair_attempt_count > 0 else "valid"


class PinResolver(Protocol):
    """Whether a price-table version exists. Satisfied by `record.RecordWriter`.

    A protocol rather than an import, so the precondition below can be tested
    without a database — the *ordering* is the requirement, and a check that
    needed a live server to exercise would be tested for the happy path only.
    """

    def pin_resolves(self, version_id: str) -> bool: ...


def require_resolvable_price_pin(config: GatewayConfig, resolver: PinResolver) -> str:
    """Refuse the invocation unless its price pin resolves (TR-048, VR-025).

    **Called before any provider request is constructed**, and the position is
    the entire requirement. The same pin discovered at write time would be a
    non-null foreign-key failure on a row for a call that had already been
    billed — and a *recorded absence* is not available for it, because the row
    cannot be written at all. That is why TR-048 closes the reasons cost may be
    absent at exactly three, none of which is "the pin did not resolve": an
    unresolvable pin never reaches a row.

    Returns:
        The resolved version identifier, so a caller cannot forget to use the
        one that was checked.

    Raises:
        GatewayConfigError: No pin is configured, or the configured pin names no
            version. Both are configuration errors on an invocation that never
            billed. The message names the pin — it is an identifier a reader
            needs and carries no credential material, unlike the connection URL
            in the same configuration.
    """
    pin = config.price_table_version_id
    if not pin:
        raise GatewayConfigError(
            f"{PRICE_TABLE_PIN_ENV_VAR} is not configured. Every recorded cost "
            f"cites the price-table version it was computed against, so an "
            f"invocation with no pin would produce a row nobody could audit."
        )
    if not resolver.pin_resolves(pin):
        raise GatewayConfigError(
            f"{PRICE_TABLE_PIN_ENV_VAR} names {pin!r}, which resolves to no "
            f"price-table version. Refused before the provider request is built "
            f"(TR-048), so this costs nothing — discovered at the record write "
            f"it would have failed an invocation that had already been billed."
        )
    return pin


class RecordSink(Protocol):
    """Writes exactly one invocation record.

    Named `RecordSink` rather than `RecordWriter` because this module now also
    imports the concrete `record.writer.RecordWriter`, and two things sharing a
    name in one module is how a signature quietly accepts the wrong one.

    A protocol rather than an import because the writer is Phase 4's, and
    because the ordering TR-008 fixes is testable now against any writer at all
    — which is the whole of what Phase 3 owes. A protocol also keeps the
    orchestrator from depending on the storage boundary in order to state the
    order in which it must be reached.
    """

    def __call__(self, *, trace_id: str, outcome: Outcome, error_type: str | None) -> None: ...


def record_then_raise(
    error: GatewayError,
    *,
    write: RecordSink,
    trace_id: str,
    outcome: Outcome,
    error_type: str | None,
) -> NoReturn:
    """Write the invocation record, then raise (TR-008, TR-036).

    The order is the requirement, and it is the order a natural implementation
    gets wrong: raising where the failure happens and recording in an `except`
    further out reads fine and leaves the record unwritten on every path that
    re-raises before reaching it.

    A record-write failure does not swallow the original. The gateway fails
    closed either way — the caller receives an error and no value — but the
    error they receive is the one that describes what actually went wrong with
    their invocation, not the storage failure that followed it. The write
    failure is attached as `__notes__`, which a traceback renders and which
    carries no reference to the write's own exception object.

    Phase 4 replaces the bare re-raise on write failure with the spool append
    of TR-041; the ordering here does not change when it does, which is why the
    ordering is being fixed now rather than then.
    """
    write_failure: BaseException | None = None
    try:
        write(trace_id=trace_id, outcome=outcome, error_type=error_type)
    except Exception as exc:  # noqa: BLE001 - the original error must still win
        write_failure = exc

    if write_failure is not None:
        # The note carries the failure's text, not the exception. Attaching the
        # object would put it in reach of a `repr` on an error whose field set
        # TR-025 closes at three scalars.
        error.add_note(
            f"the invocation record could not be written: {write_failure!s}. "
            "Phase 4 (TR-041) appends to the local spool here instead of losing it."
        )
    raise error


def _provider_boundary_is_reachable() -> bool:
    """Whether the provider boundary is importable from here.

    Exists so the composition seam has a real edge to `gateway.provider` rather
    than an imagined one: the computation-boundary contract constrains the
    graph, and a graph edge that no code creates proves nothing about the
    arrangement the contract is meant to enforce.
    """
    return hasattr(provider, "load_client_class")


# --- The mode branch, and everything downstream of it -----------------------


@dataclass(frozen=True, slots=True)
class Attempted:
    """What either arm of the mode branch produces.

    Both arms return the same three things, which is the design rather than a
    coincidence: a replayed invocation must exercise the *same* validation,
    pricing and recording a live one does, or replay proves nothing about the
    path it stands in for. Everything downstream of the branch reads this and
    never asks which arm filled it.
    """

    content: str
    usage: AttemptUsage
    pricing_timestamp: datetime
    transport_attempts: int
    resolved_model: str | None
    fixture_key: str | None


@dataclass(frozen=True, slots=True)
class Resolution:
    """The collaborators one invocation needs.

    Injected rather than reached for, so the whole path runs offline in a test
    without patching module globals — the failure paths (an unreachable
    database, a provider that refuses) are the ones worth exercising and the
    ones a real dependency cannot be asked to produce on demand.
    """

    config: GatewayConfig
    mode: str
    store: FixtureStore
    writer: RecordWriter
    spool: InvocationSpool
    now: Callable[[], datetime] = _utc_now
    monotonic: Callable[[], float] = time.monotonic

    @classmethod
    def from_environment(cls) -> Resolution:
        """Read configuration once, at the top of an invocation.

        `resolve_mode` raises when nothing is selected — no default and no
        fallback (TR-021) — so an unconfigured process fails here, before
        anything is built and before anything is billed.
        """
        config = load_config()
        return cls(
            config=config,
            mode=resolve_mode(),
            store=FixtureStore(DEFAULT_FIXTURE_ROOT),
            writer=RecordWriter(config.database_url),
            spool=InvocationSpool(config.spool_path),
        )


def _invoke(request: InvocationRequest, resolution: Resolution) -> InvocationResult:
    """One invocation, end to end.

    The order below is fixed by requirements rather than chosen, and each step
    is where it is because of what would go wrong elsewhere:

    1. **Trace identifier first**, so even the earliest failure has something to
       be recorded under (TR-031).
    2. **The price pin resolves before any request is constructed** (TR-048).
       Discovered later it would be a non-null foreign-key failure on a row for
       a call that had already been billed — and a *recorded absence* is not
       available for it, because the row cannot be written at all.
    3. **Drain the spool** while the connection is known good (TR-053). At the
       start, not the end: a drain after the write would never run on the
       invocation that failed, which is the one whose predecessors are waiting.
    4. **The mode branch.** Two arms, no default and no fallback between them.
    5. Everything after is shared, which is what makes replay evidence about the
       live path rather than about itself.
    """
    trace_id = resolve_trace_id(request)
    invocation_id = str(uuid.uuid4())

    pin = require_resolvable_price_pin(resolution.config, resolution.writer)
    _drain_quietly(resolution)

    started = resolution.monotonic()
    if resolution.mode == RECORD_MODE:
        attempted = _reach_the_provider(request, resolution)
    else:
        attempted = _resolve_from_fixtures(request, resolution)

    # TR-006: validated *before* it is returned, persisted, or logged. The
    # failure branch below writes the row and raises rather than returning, so
    # there is no path on which an unvalidated value reaches a caller.
    validated, repairs, failure = _validate(request, attempted, resolution)

    duration_ms = elapsed_ms(started, resolution.monotonic())
    outcome = classify_outcome(reached_valid_value=failure is None, repair_attempt_count=repairs)
    cost, absent_reason = _price(attempted, pin, resolution)

    record = InvocationRecord(
        invocation_id=invocation_id,
        trace_id=trace_id,
        gen_ai_provider_name=PROVIDER_NAME,
        gen_ai_operation_name=OPERATION_NAME,
        gen_ai_request_model=request.model or provider.DEFAULT_MODEL,
        gen_ai_response_model=attempted.resolved_model,
        resolution_mode=resolution.mode,
        fixture_key=attempted.fixture_key,
        gen_ai_usage_input_tokens=attempted.usage.input_tokens,
        gen_ai_usage_output_tokens=attempted.usage.output_tokens,
        cache_write_input_tokens=attempted.usage.cache_write_input_tokens,
        cache_read_input_tokens=attempted.usage.cache_read_input_tokens,
        duration_ms=duration_ms,
        transport_attempt_count=attempted.transport_attempts,
        repair_attempt_count=repairs,
        cost_usd=cost,
        cost_absent_reason=absent_reason,
        price_table_version_id=pin,
        pricing_timestamp=attempted.pricing_timestamp,
        outcome=outcome,
        error_type="validation_failed" if failure is not None else None,
        created_at=resolution.now(),
    )

    if failure is not None:
        # TR-008: the row is written *before* the error is raised, so a caller
        # that catches this can rely on the row existing — and a paid call is
        # never left with no trace of itself. The ordering is `record_then_raise`'s
        # and is tested there against a fake writer.
        log_invocation_complete(record)
        record_then_raise(
            failure,
            write=lambda **_: _write_or_spool(record, resolution),
            trace_id=trace_id,
            outcome=outcome,
            error_type="validation_failed",
        )

    _write_or_spool(record, resolution)
    log_invocation_complete(record)

    return InvocationResult(
        invocation_id=invocation_id,
        trace_id=trace_id,
        # The validated value where one was produced, and the raw content only
        # where the caller supplied no schema — in which case nothing claimed it
        # was checked. `outcome` cannot mean "schema-valid" on such a row, which
        # is why the absence of a schema is a caller decision rather than a
        # default the gateway makes for them.
        content=attempted.content if validated is None else validated.model_dump_json(),
        outcome=outcome,
        resolution_mode=resolution.mode,
    )


def _reach_the_provider(
    request: InvocationRequest,
    resolution: Resolution,
    *,
    prompt_override: str | None = None,
    is_repair: bool = False,
) -> Attempted:
    """The `record` arm: build a request, call the provider, keep the response.

    Gated twice before anything leaves the process — the mode says `record` and
    the opt-in says it is allowed (TR-027, TR-063). Two decisions rather than
    one, so reaching the provider is never a configuration slip.

    The transport budget lives in `provider.with_transport_budget`, which is
    handed a `RemainingTime` callable rather than reading a clock: TR-032 bars
    that module from `gateway.compute`, where the duration arithmetic lives.
    """
    require_provider_opt_in()
    handle = provider.CredentialHandle.from_environment()

    deadline_at = resolution.monotonic() + resolution.config.request_deadline_seconds

    def remaining() -> float:
        return deadline_at - resolution.monotonic()

    client = provider.load_client_class()(api_key=handle.reveal())
    model = request.model or provider.DEFAULT_MODEL

    def attempt(timeout: float) -> Any:
        return client.messages.create(
            model=model,
            max_tokens=DEFAULT_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt_override or request.prompt}],
            timeout=timeout,
        )

    response, attempts = provider.with_transport_budget(attempt, remaining, retryable=_is_retryable)

    content = _content_of(response)
    usage = _usage_of(response)
    recorded_at = resolution.now()
    resolved = _model_of(response, model)

    # A repair is its own fixture, keyed on the instruction that provoked it, so
    # replaying this invocation later replays the repair rather than missing it.
    key = (
        repair_fixture_key(request, prompt_override or "", schema=request.output_schema)
        if is_repair
        else fixture_key(request, schema=request.output_schema)
    )
    resolution.store.save(
        key,
        content,
        FixtureProvenance(
            recorded_on=recorded_at.date(),
            gen_ai_response_model=resolved,
            gateway_revision=_gateway_revision(),
            gen_ai_usage_input_tokens=usage.input_tokens,
            gen_ai_usage_output_tokens=usage.output_tokens,
            cache_write_input_tokens=usage.cache_write_input_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
        ),
    )

    return Attempted(
        content=content,
        usage=usage,
        pricing_timestamp=recorded_at,
        transport_attempts=attempts,
        resolved_model=resolved,
        fixture_key=key,
    )


def _resolve_from_fixtures(request: InvocationRequest, resolution: Resolution) -> Attempted:
    """The `replay` arm: resolve from the committed store, reach no network.

    Refuses to run beside a credential (TR-023). That is not paranoia about the
    credential itself — it is the only available evidence that the offline claim
    is being tested offline, since a gateway that quietly reached the provider
    would produce the same results, faster, and cost money nobody was watching
    for.

    A miss raises (TR-022). There is no fallback to the provider here, and no
    code path from this function to a socket.

    The pricing timestamp is the **fixture's recording date**, not now (TR-043),
    so replaying one fixture reproduces one cost however long afterwards it runs
    — even across an effective-from boundary inside the pinned version. The
    *duration*, by contrast, measures this replay: that is what actually
    happened.
    """
    require_no_credential_in_replay()

    fixture = resolution.store.load(fixture_key(request, schema=request.output_schema))
    return Attempted(
        content=fixture.content,
        usage=fixture.provenance.usage(),
        pricing_timestamp=fixture.provenance.pricing_timestamp(),
        # TR-056: a fixture lookup counts as one transport attempt, which is
        # what makes `transport_attempt_count >= 1` hold on a replay row without
        # anyone inferring it from the glossary.
        transport_attempts=FIXTURE_LOOKUP_ATTEMPTS,
        resolved_model=fixture.provenance.gen_ai_response_model,
        fixture_key=fixture.key,
    )


def _price(
    attempted: Attempted, pin: str, resolution: Resolution
) -> tuple[Decimal | None, str | None]:
    """Cost, or its absence with one of TR-048's three stated reasons.

    Never zero as a stand-in for unknown (TR-016): a free invocation and an
    unpriceable one are different facts, and the exclusive-or on the row is what
    keeps them distinguishable.
    """
    if attempted.resolved_model is None:
        return None, "model_unresolved"

    entries = resolution.writer.price_entries(pin, attempted.resolved_model)
    entry = resolve_price_entry(entries, attempted.resolved_model, attempted.pricing_timestamp)
    if entry is None:
        return None, "no_covering_price_entry"

    counts = TokenCounts(
        input_tokens=attempted.usage.input_tokens,
        cache_write_input_tokens=attempted.usage.cache_write_input_tokens,
        cache_read_input_tokens=attempted.usage.cache_read_input_tokens,
        output_tokens=attempted.usage.output_tokens,
    )
    try:
        return compute_cost(counts, entry.rates), None
    except CostOutOfRangeError:
        # TR-049 is explicit that this is not a rounding case: the row is
        # written with cost absent rather than with a truncated figure.
        return None, "cost_out_of_range"


def _write_or_spool(record: InvocationRecord, resolution: Resolution) -> None:
    """Commit the record, or spool it and still fail closed (TR-036, TR-041).

    Spooling does **not** soften the fail-closed rule. The caller receives an
    error and no validated value either way; what the spool changes is that the
    record of a *billed* call survives to be reconciled rather than being lost
    with the exception.
    """
    try:
        resolution.writer.write(record)
    except RecordWriteError as exc:
        resolution.spool.append(record, write_error_type=exc.failure_type or type(exc).__name__)
        raise


def _drain_quietly(resolution: Resolution) -> None:
    """Reconcile spooled records, and never fail this invocation for it (TR-054).

    The drain runs on an unrelated invocation's connection. A failure here
    belongs to a *previous* call, and letting it raise would convert a recovery
    mechanism into an outage for a caller who had nothing to do with it.
    """
    try:
        drain_spool(resolution.spool, resolution.writer)
    except Exception as exc:  # noqa: BLE001 - a drain must never fail its host
        logger.error(
            "spool drain failed; this invocation is unaffected",
            extra={"failure": type(exc).__name__},
        )


def _is_retryable(exc: BaseException) -> bool:
    """Whether a provider failure is worth another attempt.

    Read off the status rather than matched against SDK exception classes, which
    would need the distribution named in a second file (TR-001). 408, 409, 429
    and the 5xx range are transient; another 4xx is the request being wrong, and
    retrying it produces three identical rejections and three charges.
    """
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        # No status at all is a transport-level failure — a refused connection,
        # a reset, a timeout — and those are exactly what retries are for.
        return True
    return status in {408, 409, 429} or status >= 500


def _content_of(response: object) -> str:
    """The text a provider response carries.

    Defensive about shape rather than about types: the SDK's response class is
    never named here (TR-002), so this reads the documented attributes and falls
    back to the whole object's text rather than asserting a class.
    """
    blocks = getattr(response, "content", None)
    if isinstance(blocks, list) and blocks:
        text = getattr(blocks[0], "text", None)
        if isinstance(text, str):
            return text
    return str(response)


def _model_of(response: object, requested: str) -> str:
    """The model that answered, falling back to the one asked for.

    They differ when a provider resolves an alias, and the *resolved* one is
    what prices the invocation — so recording the requested name in its place
    would price against a rate that was never charged.
    """
    resolved = getattr(response, "model", None)
    return resolved if isinstance(resolved, str) and resolved else requested


def _usage_of(response: object) -> AttemptUsage:
    """Token counts, with an absent count read as zero (TR-056).

    A response that reports no usage contributes zero to each billing class
    rather than leaving the term undefined — so the sum is defined over every
    attempt rather than only the reporting ones.
    """
    usage = getattr(response, "usage", None)

    def count(name: str) -> int:
        value = getattr(usage, name, 0)
        return value if isinstance(value, int) and value >= 0 else 0

    return AttemptUsage(
        input_tokens=count("input_tokens"),
        cache_write_input_tokens=count("cache_creation_input_tokens"),
        cache_read_input_tokens=count("cache_read_input_tokens"),
        output_tokens=count("output_tokens"),
    )


def _gateway_revision() -> str:
    """The commit that produced a fixture (TR-033).

    Read from git at recording time. Falls back to a marker rather than raising:
    a fixture recorded outside a checkout is worth having with its provenance
    field honest about what it does not know, which beats no fixture at all.
    """
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - resolved from PATH
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git
        return "unknown"
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else "unknown"


def _validate(
    request: InvocationRequest, attempted: Attempted, resolution: Resolution
) -> tuple[BaseModel | None, int, GatewayValidationError | None]:
    """Validate the response, repairing at most once (TR-005 to TR-008).

    Returns `(validated, repairs, failure)`. A failure is *returned* rather than
    raised so the caller can write the row before raising — TR-008 fixes that
    order, and raising here would make the natural implementation the wrong one.

    **No schema means no validation, and that is a caller decision.** A caller
    wanting raw text is legitimate. What the gateway must not do is pretend: the
    row's `outcome` cannot mean "schema-valid" when nothing checked it, which is
    why supplying a schema is the caller's call and not a default taken for them.

    **The repair is arm-specific**, and the replay arm is the interesting one.
    """
    schema = request.output_schema
    if schema is None:
        return None, 0, None

    if resolution.mode == RECORD_MODE:
        repair = _repair_by_asking_again(request, resolution)
    else:
        repair = _repair_from_a_recorded_repair(request, resolution)

    try:
        validated, repairs = validate_or_repair(schema, attempted.content, repair)
    except GatewayValidationError as exc:
        # Returned, not raised. The row goes first (TR-008).
        return None, MAX_REPAIR_ATTEMPTS, exc
    return validated, repairs, None


def _repair_by_asking_again(
    request: InvocationRequest, resolution: Resolution
) -> Callable[[str], str]:
    """`record` mode: a repair is a second provider call.

    It carries the failing field path and the validator message (TR-007), and it
    is recorded as its own fixture — so replaying this invocation later replays
    the repair too, rather than missing on it.

    The second call gets its own transport budget, which is what TR-010's "per
    model request" means: a repair is a different request, not a retry of the
    first, and the two budgets must not consume each other.
    """

    def repair(instruction: str) -> str:
        attempted = _reach_the_provider(
            request, resolution, prompt_override=instruction, is_repair=True
        )
        return attempted.content

    return repair


def _repair_from_a_recorded_repair(
    request: InvocationRequest, resolution: Resolution
) -> Callable[[str], str]:
    """`replay` mode: a repair resolves a *second* fixture.

    There is no provider to ask, so the recorded repair is what stands in for
    one. Keyed on the original request plus the instruction that provoked it, so
    a recorded repair replays as a repair and two invocations that failed
    differently do not share one.

    **The alternative was to make replay unable to repair at all**, failing on a
    fixture that no longer validates. That was rejected: it would make
    `repaired` unreachable in the only mode continuous integration runs, so the
    outcome enumeration would be exercised nowhere a check could see it.

    A miss here raises `FixtureMissError` naming the repair key, which is the
    actionable message — it means the original was recorded before the repair
    path existed, or the schema changed, and either way the fix is to
    regenerate.
    """

    def repair(instruction: str) -> str:
        key = repair_fixture_key(request, instruction, schema=request.output_schema)
        return resolution.store.load(key).content

    return repair
