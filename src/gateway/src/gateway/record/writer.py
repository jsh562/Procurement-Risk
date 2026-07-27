"""The invocation record, committed on the gateway's own connection.

TR-035, TR-045, TR-012, TR-037, TR-043, TR-048.

**The gateway's own connection, in its own transaction** (TR-035). Not the
caller's, and this is the requirement's whole point: a caller that rolls back
its own unit of work must not thereby erase the trace of a provider call that
was billed regardless. Sharing a caller's connection would make every trace
contingent on the caller's commit, which is the one thing a trace must not be.

**Nothing is minted by the database.** No column default, no generated column,
no trigger. `invocation_id` and `created_at` arrive on the record, because a
spooled row reconciled after an outage must carry its *invocation* time and its
original identity — a `DEFAULT gen_random_uuid()` would mint a second identifier
at reconcile time and break the spool's conflict-ignoring idempotency (TR-045),
and a `DEFAULT now()` would stamp the row with its reconcile time, making
latency and cost analysis wrong by exactly the length of the outage.

**The column list is written out rather than generated from the model.** A
writer that reflected the record type would agree with it by construction and
could never detect the drift TR-068's closed field list exists to catch — the
comparison that matters is against the *migrated information schema*, which
`tests/test_read_contract.py` makes.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from types import TracebackType
from typing import Any, Final, Protocol

import psycopg

from gateway.compute.pricing import PriceEntry, PriceRates
from gateway.errors import GatewayConfigError, GatewayError
from gateway.models import InvocationRecord

__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "COLUMNS",
    "RecordWriteError",
    "RecordWriter",
    "log_invocation_complete",
    "logger",
]

#: TR-077 closes the gateway's log event set at five. Every line this package
#: emits goes through this logger, so the set is enumerable by grepping one
#: name rather than by trusting that no module reached for `logging.info`.
logger: Final[logging.Logger] = logging.getLogger("gateway.record")

#: How long to wait for the database to accept a connection.
#:
#: Stated rather than left to the driver's default, which is *no timeout* — a
#: gateway pointed at an unreachable database would block indefinitely, which
#: defeats TR-034's per-request deadline entirely and turns "the database is
#: down" into "the process is hung". Short, because the failure it is detecting
#: has a designed answer: an unreachable database routes the record to the
#: spool (TR-041), and reaching that answer quickly is the point.
CONNECT_TIMEOUT_SECONDS: Final[int] = 5

#: TR-012's field list, in the order the INSERT states it. Written out because
#: a list derived from `InvocationRecord` would agree with the model whatever
#: the model said, and the drift worth catching is between the model and the
#: *schema* — which is a different comparison, made in `test_read_contract.py`.
COLUMNS: Final[tuple[str, ...]] = (
    "invocation_id",
    "trace_id",
    "gen_ai_provider_name",
    "gen_ai_operation_name",
    "gen_ai_request_model",
    "gen_ai_response_model",
    "resolution_mode",
    "fixture_key",
    "gen_ai_usage_input_tokens",
    "gen_ai_usage_output_tokens",
    "cache_write_input_tokens",
    "cache_read_input_tokens",
    "duration_ms",
    "transport_attempt_count",
    "repair_attempt_count",
    "cost_usd",
    "cost_absent_reason",
    "price_table_version_id",
    "pricing_timestamp",
    "outcome",
    "error_type",
    "created_at",
)

#: Written out rather than joined from `COLUMNS`, following E003's convention in
#: this repository of never assembling SQL from values. The columns are constants
#: here, so a generated statement would be safe in fact — but safe by inspection
#: rather than by construction, and the literal is what a reviewer can read as
#: SQL. `test_record_writer.py` asserts the statement names exactly `COLUMNS`,
#: so the two cannot drift apart in exchange for the duplication.
_INSERT_SQL: Final[str] = """
    INSERT INTO llm_invocation (
        invocation_id, trace_id,
        gen_ai_provider_name, gen_ai_operation_name,
        gen_ai_request_model, gen_ai_response_model,
        resolution_mode, fixture_key,
        gen_ai_usage_input_tokens, gen_ai_usage_output_tokens,
        cache_write_input_tokens, cache_read_input_tokens,
        duration_ms, transport_attempt_count, repair_attempt_count,
        cost_usd, cost_absent_reason,
        price_table_version_id, pricing_timestamp,
        outcome, error_type, created_at
    ) VALUES (
        %(invocation_id)s, %(trace_id)s,
        %(gen_ai_provider_name)s, %(gen_ai_operation_name)s,
        %(gen_ai_request_model)s, %(gen_ai_response_model)s,
        %(resolution_mode)s, %(fixture_key)s,
        %(gen_ai_usage_input_tokens)s, %(gen_ai_usage_output_tokens)s,
        %(cache_write_input_tokens)s, %(cache_read_input_tokens)s,
        %(duration_ms)s, %(transport_attempt_count)s, %(repair_attempt_count)s,
        %(cost_usd)s, %(cost_absent_reason)s,
        %(price_table_version_id)s, %(pricing_timestamp)s,
        %(outcome)s, %(error_type)s, %(created_at)s
    )
"""

#: TR-052. The conflict target is the **primary key and nothing else**. Widening
#: it would make the drain swallow a genuine constraint failure, which TR-054
#: requires to be retained and surfaced rather than absorbed.
_INSERT_IDEMPOTENT_SQL: Final[str] = (
    _INSERT_SQL + " ON CONFLICT (invocation_id) DO NOTHING"
)

_RESOLVE_PIN_SQL: Final[str] = (
    "SELECT 1 FROM price_table_version WHERE version_id = %(version_id)s"
)

#: TR-039 / CD-1. Scoped to the pinned version by the `WHERE`, so the lookup
#: cannot reach outside it — `resolve_price_entry` then applies the selection
#: rule to whatever this returns, and the two together are the requirement.
_PRICE_ENTRIES_SQL: Final[str] = """
    SELECT model_id, effective_from,
           input_usd_per_mtok, cache_write_usd_per_mtok,
           cache_read_usd_per_mtok, output_usd_per_mtok
      FROM price_table_entry
     WHERE price_table_version_id = %(version_id)s
       AND model_id = %(model_id)s
"""


class RecordWriteError(GatewayError):
    """The invocation record could not be committed to PostgreSQL.

    Distinct from the errors describing what happened to the *invocation*: this
    says the call may well have succeeded and its trace did not land. TR-036
    fails the invocation closed on it either way, and TR-041 routes the record
    to the spool so the failure costs a delay rather than the record.

    **Carries the failing constraint's name, and only the name.** TR-054
    requires a retained spool row to surface "naming the failing constraint",
    and the name is available only from the driver's exception — which this
    class exists to keep from escaping. So the scalar is extracted here, at the
    one point it is in reach, and the exception is dropped: a constraint name is
    a schema identifier a reader needs, while the driver's arguments can carry
    the connection string and therefore the password.

    Without this the drain would log "something referential failed" and nothing
    about what, which is why every constraint in this epic's migrations is named
    in the first place.
    """

    def __init__(
        self,
        message: str,
        *,
        failure_type: str | None = None,
        constraint_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_type = failure_type
        self.constraint_name = constraint_name


class Connection(Protocol):
    """The shape this module needs from a database connection.

    A locally defined protocol rather than psycopg's own type, for the reason
    `ProviderClient` is one: a driver type on this module's surface would put a
    dependency in every signature a consumer reads. It also lets the tests drive
    the failure paths — a connection that raises on commit — without a database
    that can be made to fail on demand.
    """

    def execute(self, query: str, params: Any = ..., /) -> Any: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class RecordWriter:
    """Writes invocation records on a connection it owns.

    Opened lazily and held: an invocation that never writes should not pay for a
    connection, and one that writes repeatedly should not pay each time.
    """

    def __init__(self, database_url: str | None, *, connect: Any = None) -> None:
        """
        Args:
            database_url: Where to connect. `None` is a configuration error
                raised at first use rather than at construction, so a gateway
                assembled in a process that never records does not fail on
                import.
            connect: The connection factory, defaulting to `psycopg.connect`.
                Injected so the failure paths TR-036 and TR-041 exist for can be
                driven without a database that fails on command — those paths
                are the ones a live database is least able to exercise.
        """
        self._database_url = database_url
        self._connect = connect if connect is not None else psycopg.connect
        self._connection: Connection | None = None

    def __enter__(self) -> RecordWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def connection(self) -> Connection:
        """The gateway's own connection, opened on first use.

        Raises:
            GatewayConfigError: No database URL is configured. The message names
                the key and never the value — a connection URL carries a
                password, and TR-065's exclusion covers any value read from a
                credential-bearing key.
        """
        if self._connection is not None:
            return self._connection
        if not self._database_url:
            raise GatewayConfigError(
                "DATABASE_URL is not configured, so the gateway has no connection "
                "of its own to record on. Every invocation must produce a record "
                "(TR-011), so this is refused rather than run untraced."
            )
        connection: Connection = self._connect(
            self._database_url, connect_timeout=CONNECT_TIMEOUT_SECONDS
        )
        self._connection = connection
        return connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @contextmanager
    def _own_transaction(self) -> Iterator[Connection]:
        """Commit on success, roll back on failure — on this connection only.

        The independence TR-035 requires is a property of *which* connection
        this is, established at construction. What this adds is that a failure
        here leaves no partial state on it, so the next invocation's write is
        not the first thing to discover an aborted transaction.
        """
        connection = self.connection()
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()

    def write(self, record: InvocationRecord, *, idempotent: bool = False) -> None:
        """Commit one invocation record.

        Args:
            record: The row. Every value is already computed — this method does
                no arithmetic and mints nothing.
            idempotent: Ignore a primary-key conflict. Used by the drain
                (TR-052), where a row re-inserted after a crash inside the
                reconcile window is the designed recovery path rather than a
                violation. **Off by default**: on the ordinary write path a
                conflict means an invocation identifier was reused, which is a
                defect TR-045 says cannot happen and should not be silently
                absorbed.

        Raises:
            RecordWriteError: The write did not commit. Raised as a gateway
                error rather than letting the driver's escape, and carrying no
                reference to the driver exception — its arguments can include
                the connection string, which carries the password.
        """
        statement = _INSERT_IDEMPOTENT_SQL if idempotent else _INSERT_SQL
        parameters = _as_parameters(record)

        failure: str | None = None
        constraint: str | None = None
        try:
            with self._own_transaction() as connection:
                connection.execute(statement, parameters)
        except GatewayError:
            raise
        except Exception as exc:
            # Two scalars out, and the exception dropped. The *class* name and
            # the constraint name are what a reader needs; a driver error's
            # arguments can carry the connection string, and this message is
            # logged.
            failure = type(exc).__name__
            constraint = _constraint_name(exc)

        if failure is not None:
            # Raised outside the handler, as `provider.py` does and for the same
            # reason: `raise ... from None` in the block above would clear
            # `__cause__` and leave `__context__` holding the driver exception
            # this method exists to keep from escaping.
            raise RecordWriteError(
                f"the invocation record for {record.invocation_id} could not be "
                f"committed ({failure}). The invocation fails closed (TR-036) and "
                f"the record is spooled locally rather than lost (TR-041).",
                failure_type=failure,
                constraint_name=constraint,
            )

    def pin_resolves(self, version_id: str) -> bool:
        """Whether the pinned price-table version exists (TR-048).

        Asked **before any provider request is constructed**, which is the
        requirement's substance rather than an optimisation. An unresolvable pin
        discovered at write time would be a non-null foreign-key failure on a
        row for a call that had already been billed — a recorded absence is not
        available for it, because the row cannot be written at all. Asked first,
        it is a configuration error on an invocation that never happened.
        """
        with self._own_transaction() as connection:
            found = connection.execute(
                _RESOLVE_PIN_SQL, {"version_id": version_id}
            ).fetchone()
        return found is not None

    def price_entries(self, version_id: str, model_id: str) -> list[PriceEntry]:
        """Candidate rates for one model inside the pinned version (TR-039).

        The `WHERE` scopes the query to the pin, so the selection rule in
        `gateway.compute.pricing` cannot reach outside it however it is written.
        That split is deliberate: the query owns *which rows are visible* and
        the pure function owns *which of them wins*, and only the second half
        needs exhaustive testing.
        """
        with self._own_transaction() as connection:
            rows = connection.execute(
                _PRICE_ENTRIES_SQL, {"version_id": version_id, "model_id": model_id}
            ).fetchall()
        return [
            PriceEntry(
                model_id=row[0],
                effective_from=row[1],
                rates=PriceRates(
                    input_usd_per_mtok=Decimal(row[2]),
                    cache_write_usd_per_mtok=Decimal(row[3]),
                    cache_read_usd_per_mtok=Decimal(row[4]),
                    output_usd_per_mtok=Decimal(row[5]),
                ),
            )
            for row in rows
        ]


def _as_parameters(record: InvocationRecord) -> dict[str, Any]:
    """The record as bind parameters, one per column in `COLUMNS`.

    Built by reading each named column off the record rather than by dumping
    the model, so a field the model gains without a column — or a column
    without a field — raises here instead of being written as `NULL` or
    silently dropped.
    """
    return {column: getattr(record, column) for column in COLUMNS}


def _constraint_name(exc: BaseException) -> str | None:
    """The failing constraint, where the driver reports one.

    Read off psycopg's diagnostic attributes rather than parsed out of the
    message: message text is locale- and version-dependent, and a log line an
    operator greps for a constraint name must not depend on the server's
    language settings.

    `None` when the driver reports nothing — which is honest. A placeholder
    string would read like a constraint that does not exist.
    """
    diagnostic = getattr(exc, "diag", None)
    name = getattr(diagnostic, "constraint_name", None)
    return name if isinstance(name, str) and name else None


def log_invocation_complete(record: InvocationRecord) -> None:
    """TR-077 event 1: one line per invocation, at its terminal outcome.

    Emitted once per *invocation*, never once per attempt — the same unit the
    row uses (TR-042). Called at the terminal outcome rather than after the
    write, so an invocation whose record was spooled still produces its line:
    where the record landed is a separate fact, carried by event 3.

    Carries the invocation identifier **and** the trace identifier, as TR-077
    requires of this event, so a line resolves to a row and to the caller's
    trace without a text search.

    **Every field here is drawn from TR-012's closed list**, which is what makes
    TR-026's "carries no prompt or completion content" checkable rather than a
    prohibition over an open set. No prompt, no completion, no system prompt, no
    credential, no end-user identity — none of the five is in the record to
    begin with (TR-076), so no filtering step here can be forgotten.
    """
    logger.info(
        "invocation complete",
        extra={
            "invocation_id": record.invocation_id,
            "trace_id": record.trace_id,
            "outcome": record.outcome,
            "resolution_mode": record.resolution_mode,
            "gen_ai_request_model": record.gen_ai_request_model,
            "gen_ai_response_model": record.gen_ai_response_model,
            "duration_ms": record.duration_ms,
            "transport_attempt_count": record.transport_attempt_count,
            "repair_attempt_count": record.repair_attempt_count,
            "error_type": record.error_type,
        },
    )

    if record.cost_absent_reason is not None:
        _log_absent_cost(record)


def _log_absent_cost(record: InvocationRecord) -> None:
    """TR-058 / TR-077 event 2: a pinned price table that has fallen behind.

    **The symptom this exists to stop being silent.** A pin that predates a rate
    change, or predates a newly resolved model, shows up only as an absent cost
    on a row — which nobody reads until they go looking for a cost that is not
    there. The warning names the three things needed to act: the pinned version,
    the model that was not covered, and the recorded reason.

    Emitted for *every* absent-cost row, including `cost_out_of_range`, which is
    not a stale pin at all. That is deliberate: the three reasons share a
    symptom, and a warning that fired for two of the three would train a reader
    to assume the third cannot happen.
    """
    logger.warning(
        "invocation recorded with cost absent",
        extra={
            "invocation_id": record.invocation_id,
            "trace_id": record.trace_id,
            "price_table_version_id": record.price_table_version_id,
            "gen_ai_response_model": record.gen_ai_response_model,
            "cost_absent_reason": record.cost_absent_reason,
        },
    )
