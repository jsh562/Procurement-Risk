"""The fit's input: procurement lines and lifecycle events, read from the schema.

FR-001 — never the committed fixture file. The input row hash covers *these*
rows (`data-model.md` § Hashes), so hashing the file instead would let a
hand-edited row, a partial load or a database of a different vintage reproduce
cleanly with FR-023's refusal never firing.

Two things make the result hashable deterministically. The projections are
E005's own compared-content field sets, imported rather than retyped, so
`created_at` is not merely excluded from the serialization — it is never read.
And `occurred_at` is normalized to UTC here, because a `timestamptz` renders in
the session's time zone and two machines must not hash one row two ways.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from model.procurement.load import EVENT_PROJECTION, LINE_PROJECTION

__all__ = [
    "EVENTS_SQL",
    "HASHED_EVENT_FIELDS",
    "HASHED_LINE_FIELDS",
    "LINES_SQL",
    "LifecycleEventRow",
    "LineRow",
    "ProcurementInput",
    "ReadError",
    "read_lines_and_events",
]


class ReadError(RuntimeError):
    """Raised when the delivered schema cannot supply a fit's input.

    A `RuntimeError` rather than a `ValueError`: the caller passed a working
    connection and the database answered — with something no fit can be run
    against.
    """


#: The 17 line fields and 6 event fields the input row hash is defined over,
#: imported from the loader that established them. `data-model.md` § Hashes
#: names them as "E005 § Load Decisions' compared-content field sets", so the
#: two are one definition rather than two that agree today: `po_line_id` is the
#: join key, the two `GENERATED ALWAYS` columns are derived, and `created_at` is
#: a `DEFAULT now()` load-time fact that differs on every load — which is
#: precisely why the hash survives a reload of identical content.
HASHED_LINE_FIELDS: tuple[str, ...] = tuple(LINE_PROJECTION)
HASHED_EVENT_FIELDS: tuple[str, ...] = tuple(EVENT_PROJECTION)

# Module-level SQL assembled once from the two projections, following
# `model.procurement.load`: Ruff S608 exists because SQL built from *values* is
# how injection happens, and there is no value here — only two closed tuples
# this repository owns. `ORDER BY` is the canonical order the hash is taken in
# (`data-model.md` § Hashes), so the ordering is the reader's guarantee rather
# than a sort a later caller might forget.
LINES_SQL = (
    f"SELECT po_line_id, {', '.join(HASHED_LINE_FIELDS)} "  # noqa: S608
    f"FROM purchase_order_line ORDER BY project_id, po_number, line_number"
)
EVENTS_SQL = (
    f"SELECT event_id, po_line_id, {', '.join(HASHED_EVENT_FIELDS)} "  # noqa: S608
    f"FROM lifecycle_event ORDER BY po_line_id, sequence_no"
)


@dataclass(frozen=True, slots=True)
class LifecycleEventRow:
    """One lifecycle event, in the order its line walked it.

    `event_id` is carried for the join back to `purchase_order_line
    .closing_event_id` and is **outside** `HASHED_EVENT_FIELDS`: it is a
    deterministic function of the natural key, so serializing it would add no
    information the six compared fields do not already carry.
    """

    event_id: uuid.UUID
    po_line_id: uuid.UUID
    sequence_no: int
    from_state: str | None
    to_state: str
    is_terminal: bool
    occurred_at: datetime
    note: str | None


@dataclass(frozen=True, slots=True)
class LineRow:
    """One procurement line and the events it has walked so far.

    Everything the fit and the input hash need: the natural key
    `(project_id, po_number, line_number)`, the vendor and material category the
    hierarchy pools over, both dates, the closure state censoring is derived
    from, and the line's own ordered event sequence.
    """

    po_line_id: uuid.UUID
    project_id: str
    vendor_id: str
    po_number: str
    line_number: int
    material_category: str
    description: str
    manufacturer: str
    part_number: str
    quantity: Decimal
    unit_of_measure: str
    order_date: date
    need_by_date: date
    criticality: int
    lifecycle_state: str
    is_closed: bool
    closing_event_id: uuid.UUID | None
    roster_hash: str
    events: tuple[LifecycleEventRow, ...]

    @property
    def natural_key(self) -> tuple[str, str, int]:
        """`(project_id, po_number, line_number)` — the key everything sorts by."""
        return (self.project_id, self.po_number, self.line_number)


@dataclass(frozen=True, slots=True)
class ProcurementInput:
    """The whole input of one fit, in canonical order.

    Lines ascend by natural key and each line's events ascend by `sequence_no`,
    which is the order `data-model.md` § Hashes defines the input row hash over.
    The order is established once, here, rather than by whichever caller
    remembers to sort — an unsorted digest is not merely different, it is
    undefined.
    """

    lines: tuple[LineRow, ...]

    @property
    def events(self) -> tuple[LifecycleEventRow, ...]:
        """Every event, flattened in `(line, sequence_no)` order.

        Derived from `lines` rather than stored beside it, so the flat sequence
        and the per-line sequences cannot disagree about what was read.
        """
        return tuple(event for line in self.lines for event in line.events)


def _utc(moment: datetime) -> datetime:
    """An event instant in UTC, refusing a naive one.

    `lifecycle_event.occurred_at` is `timestamptz`, so the driver returns it
    aware and offset into the session's zone. Normalizing here is what makes the
    hash independent of the `TimeZone` setting of the connection that read it; a
    naive value means something upstream stripped the zone, which is a defect
    rather than a value to guess at.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ReadError(
            "`lifecycle_event.occurred_at` came back without a time zone; it is declared "
            "`timestamptz`, so a naive value means the zone was dropped between the "
            "database and here and the input hash would not survive the round trip"
        )
    return moment.astimezone(UTC)


def _events_by_line(
    connection: Connection | Session,
) -> dict[uuid.UUID, list[LifecycleEventRow]]:
    """Every lifecycle event, grouped by line and left in `sequence_no` order."""
    grouped: dict[uuid.UUID, list[LifecycleEventRow]] = defaultdict(list)
    for row in connection.execute(text(EVENTS_SQL)).mappings():
        event = LifecycleEventRow(
            event_id=row["event_id"],
            po_line_id=row["po_line_id"],
            sequence_no=row["sequence_no"],
            from_state=row["from_state"],
            to_state=row["to_state"],
            is_terminal=row["is_terminal"],
            occurred_at=_utc(row["occurred_at"]),
            note=row["note"],
        )
        grouped[event.po_line_id].append(event)
    return grouped


def read_lines_and_events(connection: Connection | Session) -> ProcurementInput:
    """Read every procurement line and its lifecycle events, in canonical order.

    Unfiltered by design. The as-of date is applied downstream by `censoring.py`,
    which decides each line's status and elapsed time from the events; filtering
    here would make the input row hash a function of the as-of date and so make
    two runs at different anchors look like runs against two different datasets.
    """
    events = _events_by_line(connection)
    lines: list[LineRow] = []
    for row in connection.execute(text(LINES_SQL)).mappings():
        po_line_id = row["po_line_id"]
        lines.append(
            LineRow(
                po_line_id=po_line_id,
                project_id=row["project_id"],
                vendor_id=row["vendor_id"],
                po_number=row["po_number"],
                line_number=row["line_number"],
                material_category=row["material_category"],
                description=row["description"],
                manufacturer=row["manufacturer"],
                part_number=row["part_number"],
                quantity=row["quantity"],
                unit_of_measure=row["unit_of_measure"],
                order_date=row["order_date"],
                need_by_date=row["need_by_date"],
                criticality=row["criticality"],
                lifecycle_state=row["lifecycle_state"],
                is_closed=row["is_closed"],
                closing_event_id=row["closing_event_id"],
                roster_hash=row["roster_hash"],
                events=tuple(events.pop(po_line_id, ())),
            )
        )

    if not lines:
        raise ReadError(
            "`purchase_order_line` is empty, so there is nothing to fit and nothing for "
            "the input row hash to cover. Load the committed dataset with "
            "`uv run --directory src/model procurement-load` before fitting."
        )
    if events:
        raise ReadError(
            f"{len(events)} lifecycle event group(s) reference a `po_line_id` that "
            f"`purchase_order_line` did not return; `fk_lifecycle_event__line` makes that "
            f"unrepresentable, so the two reads saw different states of the database"
        )
    return ProcurementInput(lines=tuple(lines))
