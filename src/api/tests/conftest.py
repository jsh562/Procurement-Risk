"""Shared fixtures for the serving boundary's tests.

Every test here runs against a real PostgreSQL with the migrated schema. There
is no fake connection and no in-memory substitute, deliberately: the worklist's
behaviour is defined by what the server returns for arrays, generated columns
and a partial unique index, and a double would be asserting agreement with
itself.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routes import worklist as worklist_route

#: A run's anchor, and the frame every date in these tests is expressed
#: against. Absolute rather than relative to the wall clock: FR-038 makes
#: `today` an input precisely so a fixture does not change state overnight,
#: and a fixture built from `date.today()` would quietly stop exercising the
#: boundary it was written for.
AS_OF = date(2026, 6, 1)
TODAY = date(2026, 6, 3)
HORIZON_DAYS = 365
DRAW_COUNT = 4000
ROSTER_HASH = "sha256:" + "a" * 64


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip(
            "DATABASE_URL is unset. These tests assert what a real PostgreSQL returns; "
            "skipping is honest, passing without a database would not be."
        )
    return url


@pytest.fixture
def connection() -> Iterator[Any]:
    """A connection whose work is rolled back.

    The transaction is never committed, so tests are order-independent and
    leave the developer's database as they found it.
    """
    with psycopg.connect(_database_url()) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def empty_worklist(connection: Any) -> Iterator[Any]:
    """A schema with no forecast run and no lines — the no-active-run state."""
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM line_posterior")
        cursor.execute("DELETE FROM forecast_run")
        cursor.execute("DELETE FROM lifecycle_event")
        cursor.execute("DELETE FROM purchase_order_line")
    yield connection


@pytest.fixture
def frozen_run(connection: Any) -> Iterator[dict[str, Any]]:
    """The committed frozen fixture, loaded through the migrated schema.

    FR-036. Every populated state's acceptance evidence runs against this one
    fixture, so a state proved here is proved against artifacts the storage
    layer would actually accept — and a boundary case with no line behind it
    fails `test_frozen_fixture` rather than passing silently.
    """
    from fixtures.frozen_run import close_fixture_line, seed_frozen_run

    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM line_posterior")
        cursor.execute("DELETE FROM forecast_run")
        cursor.execute("DELETE FROM lifecycle_event")
        cursor.execute("DELETE FROM purchase_order_line")

    document = seed_frozen_run(connection)
    anchor = date.fromisoformat(document["run"]["as_of_date"])
    for line in document["lines"]:
        if line["case"] == "terminal_line":
            close_fixture_line(connection, line["po_line_id"], anchor=anchor)

    yield document


@pytest.fixture
def client(connection: Any) -> Iterator[TestClient]:
    """A client whose requests run inside the test's own transaction.

    Overriding the dependency rather than letting the route open its own
    connection is what lets a test seed rows the request can see and still have
    them rolled back afterwards.
    """

    def _connection() -> Iterator[Any]:
        yield connection

    app.dependency_overrides[worklist_route.get_connection] = _connection
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


_INSERT_LINE_SQL = """
    INSERT INTO purchase_order_line (
        po_line_id, project_id, vendor_id, po_number, line_number,
        material_category, description, manufacturer, part_number,
        quantity, unit_of_measure, order_date, need_by_date,
        criticality, lifecycle_state, is_closed, roster_hash
    ) VALUES (
        %(po_line_id)s, %(project_id)s, %(vendor_id)s, %(po_number)s, %(line_number)s,
        'ductwork', 'Test line', 'Calvex Supply Co', 'PN-1',
        10, 'EA', %(order_date)s, %(need_by_date)s,
        %(criticality)s, %(lifecycle_state)s, false, %(roster_hash)s
    )
"""


@pytest.fixture
def insert_open_line(connection: Any) -> Callable[..., UUID]:
    """Seed one open purchase-order line and return its id.

    A fixture rather than an importable helper: the other entries' suites share
    setup through conftest and never import across test modules, and a `tests`
    package importable by name is a second, competing convention for no gain.
    """

    def _insert(
        *,
        project_id: str = "PRJ-001",
        vendor_id: str = "VND-001",
        po_number: str = "PO-1000",
        line_number: int = 1,
        need_by_date: date,
        criticality: int = 3,
        lifecycle_state: str = "submitted",
        roster_hash: str = ROSTER_HASH,
    ) -> UUID:
        po_line_id = uuid4()
        with connection.cursor() as cursor:
            cursor.execute(
                _INSERT_LINE_SQL,
                {
                    "po_line_id": po_line_id,
                    "project_id": project_id,
                    "vendor_id": vendor_id,
                    "po_number": po_number,
                    "line_number": line_number,
                    "order_date": AS_OF,
                    "need_by_date": need_by_date,
                    "criticality": criticality,
                    "lifecycle_state": lifecycle_state,
                    "roster_hash": roster_hash,
                },
            )
        return po_line_id

    return _insert


#: The shortest legal path from a submitted line to a delivered one. Not a
#: convenience: `fk_lifecycle_event__chain` requires each event's `from_state`
#: to match the previous event's `to_state`, and
#: `ck_lifecycle_event__legal_transition` restricts the pairs, so a closed line
#: is only representable at the end of a real chain.
_DELIVERY_CHAIN: Final[tuple[tuple[str | None, str], ...]] = (
    (None, "submitted"),
    ("submitted", "under_review"),
    ("under_review", "approved"),
    ("approved", "released_for_fabrication"),
    ("released_for_fabrication", "shipped"),
    ("shipped", "delivered"),
)


@pytest.fixture
def close_line(connection: Any) -> Callable[[UUID], None]:
    """Walk a line to `delivered` and point it at its terminal event.

    Two CHECK constraints pin `is_closed` from both sides — it must equal
    `lifecycle_state = 'delivered'` *and* equal `closing_event_id IS NOT NULL` —
    and a deferred `MATCH FULL` FK proves the pointed-at event is this line's and
    is terminal. So there is no shortcut: closing a line means writing the
    history that closed it, which is the point. A test that set `is_closed` by
    hand would prove the filter reads a column the test wrote, not that a
    delivered line leaves the worklist.
    """

    def _close(po_line_id: UUID) -> None:
        with connection.cursor() as cursor:
            # The FK from the line to its closing event is deferred, so the line
            # may point at an event that does not exist yet within this
            # transaction; it is proven at COMMIT — or here, at the explicit
            # constraint check, since these tests never commit.
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
            terminal_event_id: UUID | None = None
            for sequence_no, (from_state, to_state) in enumerate(_DELIVERY_CHAIN, start=1):
                event_id = uuid4()
                cursor.execute(
                    """
                    INSERT INTO lifecycle_event (
                        event_id, po_line_id, sequence_no, from_state, to_state,
                        is_terminal, occurred_at
                    ) VALUES (
                        %(event_id)s, %(po_line_id)s, %(sequence_no)s, %(from_state)s,
                        %(to_state)s, %(is_terminal)s, %(occurred_at)s
                    )
                    """,
                    {
                        "event_id": event_id,
                        "po_line_id": po_line_id,
                        "sequence_no": sequence_no,
                        "from_state": from_state,
                        "to_state": to_state,
                        "is_terminal": to_state == "delivered",
                        "occurred_at": datetime(AS_OF.year, AS_OF.month, AS_OF.day, tzinfo=UTC)
                        + timedelta(days=sequence_no),
                    },
                )
                if to_state == "delivered":
                    terminal_event_id = event_id

            cursor.execute(
                """
                UPDATE purchase_order_line
                   SET lifecycle_state = 'delivered',
                       is_closed = true,
                       closing_event_id = %(event_id)s
                 WHERE po_line_id = %(po_line_id)s
                """,
                {"event_id": terminal_event_id, "po_line_id": po_line_id},
            )

    return _close
