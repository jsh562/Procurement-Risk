"""`procurement-load` — stage, compare both ways, then refuse or insert.

**Not an upsert.** "Equal or refuse" is a set-comparison problem, not a
conflict-resolution one (AD-005): `ON CONFLICT DO NOTHING` silently tolerates a
divergent row under the same natural key, which is the precise case FR-026 must
refuse, and `DO UPDATE` performs the merge FR-030 forbids.

Four outcomes, one `REPEATABLE READ` transaction:

* a recorded generation-input digest no longer matches — **refuse**, naming it
* the database holds a natural key the fixture does not — **refuse** (FR-030)
* a key in both differs on any compared field — **refuse**, naming both (FR-026)
* a key in both matches on every compared field — **skip**, no statement issued
* a key absent from the database — **insert**, lines then events

The comparison is over an explicitly enumerated projection: 17 line fields and 6
event fields, with every exclusion either a column the database writes and the
loader cannot, or a deterministic function of a compared column.

`EXCEPT ALL` is what makes the NULL handling correct without a single `IS NULL`:
it compares rows with *not-distinct* semantics, so two NULL `closing_event_id`s
match each other, which `=` would not.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import URL, Engine, create_engine, text

from model.corpus.equipment import EQUIPMENT_MAP_INPUT_PATH
from model.corpus.manifest import sha256_of_file
from model.corpus.manufacturers import MANUFACTURER_CATALOG_INPUT_PATH
from model.procurement import paths
from model.procurement.model import NS_E005
from model.procurement.serialize import read_payload
from model.roster.reader import read_roster
from model.schema.url import get_database_url

__all__ = [
    "EVENT_PROJECTION",
    "LINE_PROJECTION",
    "LoadError",
    "LoadOutcome",
    "load",
    "main",
]

#: The 17 compared line fields, in the order `data-model.md` lists them.
#: `created_at` (load-time), the two `GENERATED ALWAYS` columns, and `po_line_id`
#: (the join key itself) are excluded and each exclusion is justified there.
LINE_PROJECTION = (
    "project_id",
    "vendor_id",
    "po_number",
    "line_number",
    "material_category",
    "description",
    "manufacturer",
    "part_number",
    "quantity",
    "unit_of_measure",
    "order_date",
    "need_by_date",
    "criticality",
    "lifecycle_state",
    "is_closed",
    "closing_event_id",
    "roster_hash",
)

#: The 6 compared event fields. `note` **is** compared: DV-022 requires it NULL
#: on every E005 event, so comparing it is free, and leaving the one uncontrolled
#: text column out would give a divergent generation a place to differ silently.
EVENT_PROJECTION = ("sequence_no", "from_state", "to_state", "is_terminal", "occurred_at", "note")

#: Every column the loader writes. The two `GENERATED ALWAYS` columns and
#: `created_at` are absent by name, so a future column addition fails loudly here
#: rather than being silently omitted.
_LINE_INSERT_COLUMNS = ("po_line_id", *LINE_PROJECTION)
_EVENT_INSERT_COLUMNS = ("event_id", "po_line_id", *EVENT_PROJECTION)

_TERMINAL_STATE = "delivered"

# ---------------------------------------------------------------------------
# Statements, assembled once from the column tuples above.
#
# Module constants rather than f-strings at each call site, following the same
# reasoning `tests/procurement/conftest.py` records for its own SQL: Ruff S608
# exists because SQL assembled from *values* is how injection happens, and there
# is no value here to assemble — only the two projections, which are closed
# tuples this module owns. Building them once also means the projection and the
# statements cannot drift apart.
# ---------------------------------------------------------------------------

_LINE_COLUMNS = ", ".join(_LINE_INSERT_COLUMNS)
_EVENT_COLUMNS = ", ".join(_EVENT_INSERT_COLUMNS)
_LINE_FIELDS = ", ".join(LINE_PROJECTION)
_EVENT_FIELDS = ", ".join(EVENT_PROJECTION)
_NATURAL = "project_id, po_number, line_number"

STAGE_LINE_DDL = (
    f"CREATE TEMP TABLE stage_line ON COMMIT DROP AS "  # noqa: S608
    f"SELECT {_LINE_COLUMNS} FROM purchase_order_line WITH NO DATA"
)
STAGE_EVENT_DDL = (
    f"CREATE TEMP TABLE stage_event ON COMMIT DROP AS "  # noqa: S608
    f"SELECT {_EVENT_COLUMNS} FROM lifecycle_event WITH NO DATA"
)
STAGE_LINE_INSERT = (
    f"INSERT INTO stage_line ({_LINE_COLUMNS}) "  # noqa: S608
    f"VALUES ({', '.join(':' + c for c in _LINE_INSERT_COLUMNS)})"
)
STAGE_EVENT_INSERT = (
    f"INSERT INTO stage_event ({_EVENT_COLUMNS}) "  # noqa: S608
    f"VALUES ({', '.join(':' + c for c in _EVENT_INSERT_COLUMNS)})"
)
FIND_EXTRA_LINES = (
    f"SELECT {_NATURAL} FROM purchase_order_line "  # noqa: S608
    f"EXCEPT ALL SELECT {_NATURAL} FROM stage_line ORDER BY {_NATURAL}"
)
FIND_DIVERGING_LINES = f"""
    WITH shared AS (
        SELECT {_NATURAL} FROM purchase_order_line
        INTERSECT
        SELECT {_NATURAL} FROM stage_line
    ),
    db AS (
        SELECT {_LINE_FIELDS} FROM purchase_order_line
        WHERE ({_NATURAL}) IN (SELECT {_NATURAL} FROM shared)
    ),
    fx AS (
        SELECT {_LINE_FIELDS} FROM stage_line
        WHERE ({_NATURAL}) IN (SELECT {_NATURAL} FROM shared)
    )
    SELECT project_id, po_number, line_number FROM (
        (SELECT * FROM db EXCEPT ALL SELECT * FROM fx)
        UNION ALL
        (SELECT * FROM fx EXCEPT ALL SELECT * FROM db)
    ) AS d
    ORDER BY project_id, po_number, line_number
"""  # noqa: S608
FIND_DIVERGING_EVENTS = f"""
    SELECT po_line_id, sequence_no FROM (
        (SELECT po_line_id, {_EVENT_FIELDS} FROM lifecycle_event
         WHERE po_line_id IN (SELECT po_line_id FROM stage_line)
         EXCEPT ALL
         SELECT po_line_id, {_EVENT_FIELDS} FROM stage_event)
        UNION ALL
        (SELECT po_line_id, {_EVENT_FIELDS} FROM stage_event
         WHERE po_line_id IN (SELECT po_line_id FROM purchase_order_line)
         EXCEPT ALL
         SELECT po_line_id, {_EVENT_FIELDS} FROM lifecycle_event)
    ) AS d
    ORDER BY po_line_id, sequence_no
"""  # noqa: S608
INSERT_LINES = f"""
    INSERT INTO purchase_order_line ({_LINE_COLUMNS})
    SELECT {_LINE_COLUMNS} FROM stage_line
    WHERE ({_NATURAL}) NOT IN (SELECT {_NATURAL} FROM purchase_order_line)
    ON CONFLICT ({_NATURAL}) DO NOTHING
"""  # noqa: S608
INSERT_EVENTS = f"""
    INSERT INTO lifecycle_event ({_EVENT_COLUMNS})
    SELECT {_EVENT_COLUMNS} FROM stage_event s
    WHERE NOT EXISTS (SELECT 1 FROM lifecycle_event e WHERE e.event_id = s.event_id)
    ORDER BY po_line_id, sequence_no
    ON CONFLICT (event_id) DO NOTHING
"""  # noqa: S608
COUNT_STAGED_LINES = "SELECT count(*) FROM stage_line"
COUNT_PRESENT_STAGED = f"""
    SELECT count(*) FROM purchase_order_line p
    WHERE (p.project_id, p.po_number, p.line_number) IN (SELECT {_NATURAL} FROM stage_line)
"""  # noqa: S608


class LoadError(RuntimeError):
    """Raised on any refusal. The transaction is rolled back before it is raised."""


@dataclass(frozen=True, slots=True)
class LoadOutcome:
    lines_inserted: int
    lines_skipped: int
    events_inserted: int


def _line_uuid(project_id: str, po_number: str, line_number: int) -> uuid.UUID:
    return uuid.uuid5(NS_E005, f"pol|{project_id}|{po_number}|{line_number}")


def _event_uuid(project_id: str, po_number: str, line_number: int, sequence_no: int) -> uuid.UUID:
    return uuid.uuid5(NS_E005, f"evt|{project_id}|{po_number}|{line_number}|{sequence_no}")


def _derive(envelope: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute every load-derived value. `uuid5`, never `uuid4` (AD-003).

    `roster_hash` comes from the **envelope**, not from the line record: FR-002
    is an obligation at the storage boundary, and 199 copies of one constant
    inside a hashed artifact is a value that can disagree with itself.
    """
    roster_entry = next(
        entry
        for entry in envelope["generation_inputs"]
        if entry["path"].endswith("project-vendor-roster.json")
    )
    roster_hash = roster_entry["digest"]

    lines: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for line in envelope["lines"]:
        key = (line["project_id"], line["po_number"], line["line_number"])
        po_line_id = _line_uuid(*key)
        chain = line["events"]
        final_state = chain[-1]["to_state"]
        is_closed = final_state == _TERMINAL_STATE
        closing_event_id = _event_uuid(*key, chain[-1]["sequence_no"]) if is_closed else None

        lines.append(
            {
                "po_line_id": po_line_id,
                "project_id": line["project_id"],
                "vendor_id": line["vendor_id"],
                "po_number": line["po_number"],
                "line_number": line["line_number"],
                "material_category": line["material_category"],
                "description": line["description"],
                "manufacturer": line["manufacturer"],
                "part_number": line["part_number"],
                # Decimal, never via float — `numeric` equality ignores trailing
                # zeros and a float round-trip is not a canonical decimal.
                "quantity": Decimal(line["quantity"]),
                "unit_of_measure": line["unit_of_measure"],
                "order_date": date.fromisoformat(line["order_date"]),
                "need_by_date": date.fromisoformat(line["need_by_date"]),
                "criticality": line["criticality"],
                "lifecycle_state": final_state,
                "is_closed": is_closed,
                "closing_event_id": closing_event_id,
                "roster_hash": roster_hash,
            }
        )

        previous: str | None = None
        for event in chain:
            events.append(
                {
                    "event_id": _event_uuid(*key, event["sequence_no"]),
                    "po_line_id": po_line_id,
                    "sequence_no": event["sequence_no"],
                    "from_state": previous,
                    "to_state": event["to_state"],
                    "is_terminal": event["to_state"] == _TERMINAL_STATE,
                    "occurred_at": _instant(event["occurred_at"]),
                    "note": None,
                }
            )
            previous = event["to_state"]

    # Events ascending by (po_line_id, sequence_no): the chain FK is NOT
    # deferrable, so event N must already see event N-1 at statement time.
    events.sort(key=lambda row: (str(row["po_line_id"]), row["sequence_no"]))
    return lines, events


def _instant(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)


def _check_input_drift(envelope: Mapping[str, Any]) -> None:
    """FR-027, at load as well as at validate. Iterates the recorded list, so an
    input added to the envelope is checked without editing this function."""
    recomputed = {
        EQUIPMENT_MAP_INPUT_PATH: lambda: sha256_of_file(
            paths.REPO_ROOT / EQUIPMENT_MAP_INPUT_PATH
        ),
        MANUFACTURER_CATALOG_INPUT_PATH: lambda: sha256_of_file(
            paths.REPO_ROOT / MANUFACTURER_CATALOG_INPUT_PATH
        ),
        "data/roster/project-vendor-roster.json": lambda: read_roster().content_hash,
    }
    for entry in envelope["generation_inputs"]:
        recompute = recomputed.get(entry["path"])
        if recompute is None:
            raise LoadError(
                f"the envelope records generation input {entry['path']!r}, which this loader "
                f"does not know how to recompute; an unverifiable provenance value is worse "
                f"than an absent one"
            )
        actual = recompute()
        if actual != entry["digest"]:
            raise LoadError(
                f"generation input {entry['path']} has drifted: the fixture records "
                f"{entry['digest']} ({entry['digest_kind']}) and the file now digests to "
                f"{actual}. Refusing rather than loading a dataset whose inputs moved"
            )


def _engine(url: str | URL | None = None) -> Engine:
    """Resolve the connection target, accepting either form.

    `get_database_url()` returns a SQLAlchemy `URL`, not a string — the entry
    point path went straight to `.startswith` and failed with an AttributeError
    the first time it ran outside a test, because every test supplies a string.
    Both forms are handled here rather than at each call site.
    """
    resolved = get_database_url() if url is None else url
    if isinstance(resolved, URL):
        if resolved.drivername == "postgresql":
            resolved = resolved.set(drivername="postgresql+psycopg")
        return create_engine(resolved, future=True)
    if resolved.startswith("postgresql://"):
        resolved = resolved.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(resolved, future=True)


def load(fixture: Path | None = None, url: str | URL | None = None) -> LoadOutcome:
    """Load the committed fixture. One transaction; every refusal rolls back."""
    envelope = read_payload(fixture or paths.fixture_path())
    _check_input_drift(envelope)
    lines, events = _derive(envelope)

    engine = _engine(url)
    with engine.begin() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        # UTC pinned for the session, so a server in another zone renders the
        # same instants and the comparison is not zone-dependent.
        connection.execute(text("SET LOCAL TimeZone = 'UTC'"))

        _stage(connection, lines, events)
        _reconcile(connection)
        return _insert(connection)


def _stage(connection, lines: Sequence[dict], events: Sequence[dict]) -> None:
    """Stage into `TEMP` tables that hold only the columns the loader writes.

    `SELECT ... WITH NO DATA` rather than `LIKE ... EXCLUDING ALL`: `LIKE` copies
    column properties including `NOT NULL`, but `EXCLUDING ALL` drops `DEFAULT`s
    — so the staging table would demand `created_at` and have no default to
    supply it, and the insert fails on a column the loader is forbidden to write.
    """
    connection.execute(text(STAGE_LINE_DDL))
    connection.execute(text(STAGE_EVENT_DDL))
    if lines:
        connection.execute(text(STAGE_LINE_INSERT), list(lines))
    if events:
        connection.execute(text(STAGE_EVENT_INSERT), list(events))


def _reconcile(connection) -> None:
    """`EXCEPT ALL` in both directions over the enumerated projection."""
    # FR-030: the database holds a natural key the fixture does not. Over the
    # whole table — E005 is its only writer, so no scoping predicate is needed.
    extra = connection.execute(text(FIND_EXTRA_LINES)).all()
    if extra:
        raise LoadError(
            f"the database holds {len(extra)} purchase-order line(s) the fixture does not "
            f"contain, so the fixture is not a superset of what is stored. First few: "
            f"{[tuple(row) for row in extra[:5]]}. Refusing rather than leaving them "
            f"orphaned or deleting them (FR-030)"
        )

    # FR-026: a key in both, differing on any compared field. Both directions, so
    # a difference is caught whichever side carries the unexpected value.
    diverging = connection.execute(text(FIND_DIVERGING_LINES)).all()
    if diverging:
        raise LoadError(
            f"{len(diverging)} purchase-order line(s) share a natural key with the fixture "
            f"but differ on a compared field: {[tuple(row) for row in diverging[:5]]}. "
            f"Refusing rather than updating — a divergent row under the same key is a "
            f"different dataset, not a stale one (FR-026)"
        )

    diverging_events = connection.execute(text(FIND_DIVERGING_EVENTS)).all()
    if diverging_events:
        raise LoadError(
            f"{len(diverging_events)} lifecycle event(s) diverge from the fixture: "
            f"{[tuple(row) for row in diverging_events[:5]]}. Refusing (FR-026)"
        )


def _insert(connection) -> LoadOutcome:
    """Lines first, then events ascending. No `GENERATED` or `DEFAULT` column named."""
    lines_inserted = connection.execute(text(INSERT_LINES)).rowcount

    staged_lines = connection.execute(text(COUNT_STAGED_LINES)).scalar_one()
    already = connection.execute(text(COUNT_PRESENT_STAGED)).scalar_one()
    if already != staged_lines:
        raise LoadError(
            f"after insert, {already} of {staged_lines} staged lines are present. The "
            f"`ON CONFLICT DO NOTHING` guard absorbed a concurrent writer's row, which "
            f"means another process is writing this table"
        )

    events_inserted = connection.execute(text(INSERT_EVENTS)).rowcount
    return LoadOutcome(
        lines_inserted=lines_inserted,
        lines_skipped=staged_lines - lines_inserted,
        events_inserted=events_inserted,
    )


def main() -> int:
    try:
        outcome = load()
    except LoadError as error:
        print(f"procurement-load refused: {error}")
        return 1
    print(
        f"loaded {outcome.lines_inserted} line(s), skipped {outcome.lines_skipped}, "
        f"inserted {outcome.events_inserted} event(s)"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
