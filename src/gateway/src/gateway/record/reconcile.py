"""Draining the spool into the invocation table, exactly once in effect.

TR-052, TR-053, TR-054, TR-077.

**Exactly-once is a property of the *effect*, not of delivery** (TR-052). At
most one invocation row per invocation identifier — that is the claim, and it
rests on two things together: the identifier is minted once per invocation and
reused unchanged (TR-045), and the insert ignores a primary-key conflict. A
spool row re-inserted after a crash inside the reconcile window is therefore the
designed recovery path rather than a violation, and must not be read as one.

**Delete only after the invocation-table transaction has committed** (TR-052).
The ordering is the whole mechanism. Deleting first would lose the record if the
insert then failed; deleting after means a crash between the two leaves a
spooled row whose record is already committed, and the next drain's conflict-
ignoring insert absorbs it. One ordering loses data on a crash and the other
does redundant work — the choice is not close.

**One bad row must not become an outage** (TR-054). A record that fails a
referential check is retained, surfaced as a logged error naming the failing
constraint and the invocation identifier, and *stepped over*: the drain
continues with the remaining rows and the unrelated invocation whose connection
triggered it is unaffected. A recovery mechanism that fails the caller it was
recovering behind would be worse than no recovery mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from gateway.record.spool import PAYLOAD_SCHEMA_VERSION, InvocationSpool, SpooledRecord
from gateway.record.writer import RecordWriter, logger

__all__ = ["DrainResult", "drain_spool"]

#: Payload shapes this gateway can read. A drain encountering anything else
#: retains the row and surfaces it, rather than reading it under a different
#: shape or discarding it (TR-054) — so a spool written before an upgrade has a
#: defined outcome, and the operator gets to decide what that outcome is.
UNDERSTOOD_PAYLOAD_VERSIONS: Final[frozenset[int]] = frozenset({PAYLOAD_SCHEMA_VERSION})


@dataclass(frozen=True, slots=True)
class DrainResult:
    """What one drain did. Returned rather than only logged, so a test can
    assert the outcome without parsing a log line."""

    reconciled: int
    retained: int
    depth_before: int
    depth_after: int


def drain_spool(spool: InvocationSpool, writer: RecordWriter) -> DrainResult:
    """Move every readable spooled record into the invocation table.

    Called at the start of every invocation that successfully opens the
    gateway's database connection (TR-053) — **not** on a timer, thread, or
    background process, because the gateway is a library with no runtime of its
    own. The consequence is stated rather than hidden: when no further
    invocation occurs after an outage ends, spooled records stay durably spooled
    until one does. Nothing is lost and nothing drains unattended, and the depth
    logged on every write and every drain is what makes that condition visible.

    Concurrency (TR-053): a second gateway process draining the same spool is
    tolerated rather than excluded. SQLite's own transactions serialize the
    claims, a duplicate reconcile is absorbed by the conflict-ignoring insert,
    and a delete of an already-deleted row is a no-op. No lock file, no advisory
    lock, no leader election — the three properties above make them unnecessary.

    Returns:
        Counts and depths. `retained` is the number of rows deliberately left
        behind, which is a number an operator needs and a silent drain does not
        give them.
    """
    depth_before = spool.depth()
    reconciled = 0
    retained = 0

    for spooled in spool.pending():
        if _reconcile_one(spooled, spool, writer):
            reconciled += 1
        else:
            retained += 1

    depth_after = spool.depth()

    # TR-077 event 4. Emitted even when nothing was waiting: a drain that
    # reports only when it moves something makes "the spool is empty" and "the
    # drain never ran" the same observation.
    logger.info(
        "spool drained",
        extra={
            "spool_depth_before": depth_before,
            "spool_depth_after": depth_after,
            "reconciled": reconciled,
            "retained": retained,
        },
    )
    return DrainResult(
        reconciled=reconciled,
        retained=retained,
        depth_before=depth_before,
        depth_after=depth_after,
    )


def _reconcile_one(
    spooled: SpooledRecord, spool: InvocationSpool, writer: RecordWriter
) -> bool:
    """Insert one spooled record and drop it. Returns whether it was reconciled.

    Every failure path here **retains** the row and returns False. None of them
    re-raises: TR-054 forbids failing the unrelated invocation whose connection
    triggered this drain, and a raise would do exactly that.
    """
    if spooled.payload_schema_version not in UNDERSTOOD_PAYLOAD_VERSIONS:
        # TR-054: retained and surfaced, never read under a different shape and
        # never discarded. A payload from a newer gateway is not corrupt — it is
        # simply not this process's to interpret.
        logger.error(
            "spooled record retained: payload schema version is not understood",
            extra={
                "invocation_id": spooled.invocation_id,
                "payload_schema_version": spooled.payload_schema_version,
                "understood_versions": sorted(UNDERSTOOD_PAYLOAD_VERSIONS),
            },
        )
        return False

    try:
        record = spooled.to_record()
    except Exception as exc:  # noqa: BLE001 - any decode failure retains the row
        logger.error(
            "spooled record retained: payload could not be read as an invocation record",
            extra={
                "invocation_id": spooled.invocation_id,
                "failure": type(exc).__name__,
            },
        )
        return False

    try:
        # Idempotent: a primary-key conflict means this record's row is already
        # committed, which is the crash-inside-the-window case and is success,
        # not failure. The conflict target is the primary key and nothing else,
        # so a *referential* failure still raises and is handled below —
        # widening it is what TR-054 forbids.
        writer.write(record, idempotent=True)
    except Exception as exc:  # noqa: BLE001 - one bad row must not end the drain
        # TR-054: names the failing constraint and the invocation identifier.
        # The constraint name is why every constraint in this epic's migrations
        # is named — a server-generated name here would tell an operator that
        # something referential failed and nothing about what.
        logger.error(
            "spooled record retained: reconcile failed a database check",
            extra={
                "invocation_id": spooled.invocation_id,
                "failure": type(exc).__name__,
                "constraint": _failing_constraint(exc),
            },
        )
        return False

    # Only now, and the position of this line is the requirement (TR-052). The
    # invocation-table transaction has committed, so deleting the spool row
    # cannot lose the record. A crash between the two leaves a spooled row whose
    # record is already committed, which the next drain's conflict-ignoring
    # insert absorbs — redundant work rather than lost data.
    spool.discard(spooled.invocation_id)
    return True


def _failing_constraint(exc: BaseException) -> str:
    """The constraint name TR-054 requires the log line to carry.

    Read off `RecordWriteError`, which extracted it at the one point the
    driver's exception was in reach and then dropped that exception. Inspecting
    the driver's own diagnostics *here* would find nothing — by design, since
    letting the driver exception travel this far is what the writer's
    normalization exists to prevent.
    """
    name = getattr(exc, "constraint_name", None)
    if isinstance(name, str) and name:
        return name
    return "unreported-by-driver"
