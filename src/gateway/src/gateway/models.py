"""Gateway-owned request, result, and record types.

TR-002. Every value crossing the gateway's public surface is defined here, so a
consumer can be built and type-checked with no provider package installed. That
is what makes the provider-type-free claim testable rather than asserted: while
the SDK was a hard dependency, anything able to import the gateway also had it,
and the harness had nothing to assert (ADR-0014).

Nothing here may import the provider SDK, at module scope or under
``TYPE_CHECKING``: ``import-linter``'s ``exclude_type_checking_imports``
defaults to false, so a guarded import violates the contract just as a real one
does, and a leaked SDK type in an annotation couples every consumer to the
provider.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "TRACE_ID_PATTERN",
    "CostAbsentReason",
    "InvocationRecord",
    "InvocationRequest",
    "InvocationResult",
    "Outcome",
    "ResolutionMode",
    "generate_trace_id",
    "is_valid_trace_id",
]

#: TR-047. Thirty-two lowercase hexadecimal characters. Matches the W3C Trace
#: Context trace-id and, conveniently, ``uuid4().hex`` — but the domain is
#: specified independently of how one is generated, because a caller may supply
#: an identifier this gateway did not mint.
TRACE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{32}$")

#: The one value the pattern admits and the domain does not. W3C reserves
#: all-zero as "invalid", and a column full of zeroes would satisfy TR-031's
#: NOT NULL constraint while carrying no information at all — the failure the
#: constraint exists to prevent, wearing a valid-looking value.
_ALL_ZERO_TRACE_ID: Final[str] = "0" * 32


def is_valid_trace_id(value: str) -> bool:
    """Whether ``value`` is inside TR-047's domain.

    Exposed rather than inlined into the validator so the record writer and the
    checks can ask the same question of a stored value without restating the
    rule. Two copies of a domain rule is one copy too many.
    """
    return bool(TRACE_ID_PATTERN.match(value)) and value != _ALL_ZERO_TRACE_ID


def generate_trace_id() -> str:
    """Mint an identifier inside TR-047's domain.

    ``uuid4().hex`` is 32 lowercase hex by construction. The all-zero case is
    excluded explicitly rather than dismissed as improbable: TR-047 states the
    exclusion, so the generator honours it rather than relying on the odds.
    """
    while True:
        candidate = uuid.uuid4().hex
        if candidate != _ALL_ZERO_TRACE_ID:
            return candidate


type ResolutionMode = str
"""How an invocation was resolved: ``record`` reaches the provider, ``replay``
resolves from a committed fixture. Widened to a closed enumeration in Phase 5,
where mode selection is implemented (TR-021)."""

type Outcome = str
"""``valid``, ``repaired`` or ``failed`` — the classification of an invocation,
never of an attempt (TR-009, TR-042). Widened to a closed enumeration in Phase
3, where the terminal-state mapping is implemented (TR-078)."""


class InvocationRequest(BaseModel):
    """What a caller asks the gateway to do.

    ``trace_id`` is an **explicit optional field**, never ambient (TR-080). The
    gateway reads no thread-local, no context variable and no propagator: a
    caller that wants its request correlated says so in the request, and one
    that does not gets a generated identifier. Ambient propagation would make
    the identifier's provenance invisible at the call site, and it is the one
    field a reader most needs to trace back.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    prompt: str = Field(min_length=1)
    model: str | None = None
    trace_id: Annotated[str | None, Field(default=None)] = None
    """The identifier this invocation is correlated under (TR-031, TR-047, TR-080).

    **Serialized, and deliberately not hashed.** It is a declared field, so
    TR-020's closure would put it in the fixture key; it is named in
    `compute.hashing.UNHASHED_REQUEST_FIELDS` instead, because a correlation
    identifier says which *run* observed the call and reaches neither the
    provider request nor the provider's answer. Hashing it meant two requests
    that would produce the identical provider call keyed differently once per
    run — FR-070 obliges one run-scoped identifier per run — so no recorded
    fixture was replayable by any caller that supplied one.

    **Do not resolve that by adding `exclude=True` here.** The exclusion belongs
    to the hash, not to the type: `exclude=True` would drop the identifier from
    every serialization of a request, including any log line or spooled payload,
    and the field a reader most needs to trace back is the last one that should
    silently vanish from a dump (Principle I). `output_schema` below is excluded
    on the model for the unrelated reason that a class is not JSON.
    """

    output_schema: Annotated[type[BaseModel] | None, Field(default=None, exclude=True)] = None
    """The schema every output is validated against before it is returned (TR-006).

    **Optional, and its absence means something.** A caller that wants raw text
    is legitimate, so `None` skips validation rather than failing — but it also
    means the gateway is returning a value it has not checked, which is why the
    invocation record's `outcome` cannot mean "schema-valid" on such a row.
    Callers that care should supply one.

    **Excluded from serialization, and it has to be** (TR-020). `fixture_key`
    hashes `model_dump_json()`, and a class is not JSON. The schema still
    reaches the key — through its *digest*, passed as `fixture_key`'s `schema`
    argument, which is what TR-038 requires ("a digest over the full schema
    definition including its post-decode validators, and MUST NOT accept either
    as a caller-declared string"). So this field is covered by digest rather
    than by value, which is the stronger of the two.

    Note that this exclusion is **not** the exception `UNHASHED_REQUEST_FIELDS`
    records against `trace_id`. This field is excluded from serialization and
    still reaches the key; that one is serialized and deliberately does not.
    Reading them as one rule is how someone concludes the model is the place to
    exclude a field from the hash.

    `arbitrary_types_allowed` is on for this field alone. A model *class* is not
    a pydantic-native annotation, and the alternative — accepting a raw JSON
    Schema mapping — was rejected in plan AD-010 for needing a validator
    dependency to enforce post-decode what pydantic already enforces.
    """

    @field_validator("trace_id")
    @classmethod
    def _trace_id_is_in_domain(cls, value: str | None) -> str | None:
        """TR-047: validate a caller-supplied identifier at the boundary.

        Before use, not at write time. A malformed identifier caught here costs
        nothing; caught at the storage boundary it costs a provider call whose
        record cannot be written, which is the billed-but-untraced case
        ADR-0015 exists to prevent.
        """
        if value is None:
            return None
        if not is_valid_trace_id(value):
            raise ValueError(
                "trace_id must be 32 lowercase hexadecimal characters and not all zero; "
                f"got {len(value)} characters"
            )
        return value


class InvocationResult(BaseModel):
    """What the gateway returns when an invocation succeeds.

    Only a validated value reaches a caller (TR-006). A failure raises rather
    than returning a result carrying an error, so a caller cannot mistake one
    for the other by forgetting to check a field.

    ``invocation_id`` and ``trace_id`` are here so the caller can join what it
    received to the row the gateway wrote, without querying for it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    invocation_id: str
    trace_id: str
    content: str
    outcome: Outcome
    resolution_mode: ResolutionMode


type CostAbsentReason = str
"""``no_covering_price_entry``, ``model_unresolved`` or ``cost_out_of_range`` —
the closed set of reasons a written row may carry no cost (TR-016, TR-048). An
unresolvable price pin is deliberately **not** a fourth value: TR-048 refuses it
before any request is constructed, so it never reaches a row."""


class InvocationRecord(BaseModel):
    """One row of ``llm_invocation`` — TR-012's field list, closed.

    **Closed in the sense TR-068 means it**: these are exactly the fields the
    row carries, not a lower bound. Nothing is recorded outside this list, and
    adding, removing, or renaming one is an amendment to TR-012 itself. That
    closure is what makes E013's read contract *checkable* — a column in the
    schema and absent here is a defect in one of the two, found by comparing
    two sets rather than by reading both and hoping.

    ``extra="forbid"`` is the closure as far as a type can carry it: a field
    name that drifts from the column set is rejected at construction rather
    than written and silently ignored. `tests/test_read_contract.py` carries
    the rest, comparing this list against the migrated information schema.

    Every value here is computed **before** the write. The database mints
    nothing: no default, no generated column, no trigger. `invocation_id` and
    `created_at` in particular are gateway-generated, because a spooled row
    reconciled after an outage must carry its *invocation* time rather than its
    reconcile time (TR-041, TR-043, TR-045).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Identity. Minted once per invocation, before the first write is
    # attempted, and reused unchanged by the spool copy and by the row it
    # reconciles into (TR-045). This is the uniqueness key behind TR-011's
    # "exactly one row per invocation": a second row is unrepresentable because
    # no second identifier is ever minted.
    invocation_id: str
    trace_id: str

    # Convention-named (TR-013, TR-071), spelled from the pinned release.
    gen_ai_provider_name: str
    gen_ai_operation_name: str
    gen_ai_request_model: str
    gen_ai_response_model: str | None = None

    # How the invocation was resolved, and from what (TR-037).
    resolution_mode: ResolutionMode
    fixture_key: str | None = None

    # Usage, summed across every attempt rather than taken from the last
    # (TR-040). Zero is a value; unknown is not.
    gen_ai_usage_input_tokens: int = Field(ge=0)
    gen_ai_usage_output_tokens: int = Field(ge=0)
    cache_write_input_tokens: int = Field(ge=0)
    cache_read_input_tokens: int = Field(ge=0)

    # Wall clock across every attempt, from a monotonic clock (TR-056).
    duration_ms: int = Field(ge=0)

    # The only per-attempt information stored. No attempt-level outcome value
    # exists anywhere (TR-042).
    transport_attempt_count: int = Field(ge=1, le=3)
    repair_attempt_count: int = Field(ge=0, le=1)

    # Cost, or its absence with a stated reason — never zero as a stand-in
    # (TR-016). The exclusive-or is enforced by the column pair's CHECK and
    # again by the validator below, so a malformed pair fails before the write
    # rather than as a constraint violation on an invocation already billed.
    cost_usd: Decimal | None = None
    cost_absent_reason: CostAbsentReason | None = None
    price_table_version_id: str
    pricing_timestamp: datetime

    # Terminal classification (TR-009, TR-078) and its paired cause.
    outcome: Outcome
    error_type: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _cost_is_present_or_explained(self) -> InvocationRecord:
        """TR-016's exclusive-or, checked here as well as in the database.

        Not redundant with the column `CHECK`. The constraint is the backstop;
        this is the gate. A malformed pair caught here costs nothing, while the
        same pair caught at the storage boundary costs a provider call whose
        record cannot be written — the billed-but-untraced case the spool exists
        to prevent, arrived at by a different route.
        """
        if (self.cost_usd is None) == (self.cost_absent_reason is None):
            raise ValueError(
                "exactly one of cost_usd and cost_absent_reason must be set: a "
                "cost with a reason is contradictory, and an absent cost without "
                "one is the unexplained absence TR-016 forbids"
            )
        return self

    @model_validator(mode="after")
    def _error_type_is_present_exactly_when_failed(self) -> InvocationRecord:
        """The biconditional the column pair carries (TR-012, OBJ3 VC8).

        Enforced in the same direction as the database so a row cannot be built
        here that the write would reject — the two would otherwise disagree only
        on the failure path, which is the path least likely to be exercised.
        """
        if (self.outcome == "failed") != (self.error_type is not None):
            raise ValueError(
                f"error_type must be present exactly when outcome is 'failed'; "
                f"got outcome={self.outcome!r} with "
                f"error_type={'set' if self.error_type else 'unset'}"
            )
        return self
