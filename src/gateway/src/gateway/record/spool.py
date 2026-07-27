"""The local append-only spool: where a billed call's record waits.

TR-041, TR-045, TR-052, TR-053. {SAD:ADR-0015}.

**The one case this exists for.** `record` mode, a valid credential, a schema-
valid response, and the database unreachable. The provider has been paid; the
row cannot be written. Without a spool, TR-011's hundred-percent tracing claim
is false on exactly the failure the claim exists to exclude — and the
alternative, narrowing the claim's denominator, puts an asterisk on the
product's loudest guarantee.

**Not a second datastore of record.** `project-instructions.md` forbids one and
ADR-0015 scopes the rule rather than bending it, with a three-part test this
file is built to satisfy: the spool holds only *unreconciled* records, no
consumer ever queries it, and its steady state is empty. It is a buffer with a
drain, not a store.

**Append-only means a row is inserted once and removed once** (TR-052). Nothing
here updates a spooled row in place. There is no attempt counter, no status
column, no "last tried" timestamp — each would be an update, and each would
make a crashed drain leave a row in a state the next drain has to interpret.

**No timer, no thread, no background process** (TR-053). The gateway is a
library with no runtime of its own, so the drain is triggered by the next
invocation that opens a connection. When no further invocation happens after an
outage ends, spooled records stay durably spooled until one does — nothing is
lost and nothing drains unattended. Spool depth is logged on every write and
every drain precisely so that condition is visible rather than silent.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from gateway.models import InvocationRecord
from gateway.record.writer import logger

__all__ = [
    "PAYLOAD_SCHEMA_VERSION",
    "InvocationSpool",
    "SpooledRecord",
]

#: The shape `payload` is written under (TR-054). A drain reconciles only
#: versions it understands and **retains** an unrecognised one as a loud error
#: rather than reading it under a different shape or discarding it — so a spool
#: written before a gateway upgrade has a defined outcome instead of a guess.
PAYLOAD_SCHEMA_VERSION: Final[int] = 1

DEFAULT_SPOOL_FILENAME: Final[str] = "gateway-invocation-spool.sqlite3"

#: `IF NOT EXISTS` because this table cannot come from the Postgres migration
#: runner — it is needed at precisely the moment Postgres is unreachable, which
#: is also why it is created by the gateway on open rather than by a migration.
_CREATE_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS invocation_spool (
        invocation_id TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        payload_schema_version INTEGER NOT NULL,
        spooled_at TEXT NOT NULL,
        write_error_type TEXT NOT NULL
    )
"""

_INSERT_SQL: Final[str] = """
    INSERT INTO invocation_spool (
        invocation_id, payload, payload_schema_version, spooled_at, write_error_type
    ) VALUES (?, ?, ?, ?, ?)
    ON CONFLICT (invocation_id) DO NOTHING
"""

_SELECT_SQL: Final[str] = """
    SELECT invocation_id, payload, payload_schema_version, spooled_at, write_error_type
      FROM invocation_spool
     ORDER BY spooled_at, invocation_id
"""

_DELETE_SQL: Final[str] = "DELETE FROM invocation_spool WHERE invocation_id = ?"

_DEPTH_SQL: Final[str] = "SELECT count(*) FROM invocation_spool"


@dataclass(frozen=True, slots=True)
class SpooledRecord:
    """One waiting record, as the drain reads it."""

    invocation_id: str
    payload: str
    payload_schema_version: int
    spooled_at: str
    write_error_type: str

    def to_record(self) -> InvocationRecord:
        """Rebuild the invocation record from its stored payload.

        Validated on the way back in, not merely deserialized: a payload that no
        longer satisfies the record type is a payload written by a different
        gateway, and reading it as though it did is the case TR-054 forbids.
        """
        return InvocationRecord.model_validate_json(self.payload)


class InvocationSpool:
    """A local, append-only queue of records waiting for the database.

    Opened per use rather than held: the spool is touched on the rare failure
    path and on each drain, so a persistent handle would keep a file lock open
    across the entire life of a long-running consumer for no benefit — and would
    make the concurrent-drain case of TR-053 harder rather than easier.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else Path(DEFAULT_SPOOL_FILENAME)

    def _connect(self) -> sqlite3.Connection:
        """Open the spool, creating the file and the table if absent.

        `WAL` and `synchronous=FULL` together (ADR-0015): WAL so a reader and
        the writer do not block each other during a drain, and `FULL` because
        the entire purpose of this file is to survive the crash that follows the
        failure that filled it. `NORMAL` would let the operating system hold a
        just-spooled record in a buffer that a power loss discards — which is
        the one loss this file exists to prevent.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(_CREATE_SQL)
        return connection

    def append(self, record: InvocationRecord, *, write_error_type: str) -> int:
        """Spool a record whose database write failed, and return the new depth.

        `ON CONFLICT DO NOTHING` on the primary key: the invocation identifier
        is minted once per invocation (TR-045), so a second append under the
        same identifier is a retry of the same failure rather than a second
        record. Absorbing it keeps the spool append-only in the sense TR-052
        means — inserted once, removed once, never updated in place.

        Args:
            record: The row that could not be committed.
            write_error_type: Why the database write failed. A *class* name,
                never a driver exception's arguments, which can carry the
                connection string and therefore the password.

        Returns:
            The spool depth after the write, which the caller logs (TR-053,
            TR-077 event 3).
        """
        with closing(self._connect()) as connection:
            connection.execute(
                _INSERT_SQL,
                (
                    record.invocation_id,
                    record.model_dump_json(),
                    PAYLOAD_SCHEMA_VERSION,
                    datetime.now(UTC).isoformat(),
                    write_error_type,
                ),
            )
            depth = self._depth(connection)

        # TR-077 event 3: carries the invocation identifier as its correlating
        # field, so the line resolves to a row without a text search.
        logger.warning(
            "invocation record spooled locally; database write failed",
            extra={
                "invocation_id": record.invocation_id,
                "write_error_type": write_error_type,
                "spool_depth": depth,
            },
        )
        return depth

    def pending(self) -> Iterator[SpooledRecord]:
        """Every waiting record, oldest first.

        Materialized before yielding rather than streamed from an open cursor:
        the drain deletes rows as it goes, and mutating the table a live cursor
        is reading is how a drain silently skips half its own work.
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(_SELECT_SQL).fetchall()
        for row in rows:
            yield SpooledRecord(*row)

    def discard(self, invocation_id: str) -> None:
        """Remove a record whose row has committed (TR-052).

        Called **only after** the invocation-table transaction has committed,
        which is the ordering that makes the effect exactly-once. A delete of an
        already-deleted row is a no-op, so a second gateway process draining the
        same spool concurrently is absorbed rather than an error (TR-053).
        """
        with closing(self._connect()) as connection:
            connection.execute(_DELETE_SQL, (invocation_id,))

    def depth(self) -> int:
        """How many records are waiting. Zero in steady state (ADR-0015)."""
        with closing(self._connect()) as connection:
            return self._depth(connection)

    @staticmethod
    def _depth(connection: sqlite3.Connection) -> int:
        row = connection.execute(_DEPTH_SQL).fetchone()
        return int(row[0]) if row else 0

