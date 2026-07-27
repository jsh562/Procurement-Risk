"""SC-008 — the committed fixture loads into an empty migrated database.

Every delivered constraint is enforced during this load and none is disabled:
DV-004's presence checks, DV-005's positivity, DV-007's transition legality and
first-event shape all run as `CHECK`s and triggers on the real insert path. A
load that passed with constraints dropped would demonstrate nothing.
"""

from __future__ import annotations

import pytest

from model.procurement import paths
from model.procurement.load import LINE_PROJECTION, load
from model.procurement.serialize import read_payload

pytestmark = pytest.mark.usefixtures("empty_procurement_tables")


@pytest.fixture(scope="module")
def envelope():
    return read_payload(paths.fixture_path())


def _scalar(connection, sql: str):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    return row[0]


class TestFirstLoad:
    def test_the_fixture_loads(self, database_url, envelope, pg_connection) -> None:
        outcome = load(url=str(database_url.render_as_string(hide_password=False)))
        assert outcome.lines_inserted == len(envelope["lines"])
        assert outcome.lines_skipped == 0
        assert outcome.events_inserted == sum(len(x["events"]) for x in envelope["lines"])

    def test_row_counts_match_the_fixture(self, database_url, envelope, pg_connection) -> None:
        load(url=str(database_url.render_as_string(hide_password=False)))
        assert _scalar(pg_connection, "SELECT count(*) FROM purchase_order_line") == len(
            envelope["lines"]
        )
        assert _scalar(pg_connection, "SELECT count(*) FROM lifecycle_event") == sum(
            len(x["events"]) for x in envelope["lines"]
        )

    def test_no_constraint_is_disabled(self, pg_connection) -> None:
        """A load into a database with `CHECK`s dropped proves nothing, so the
        state of the constraints is asserted rather than assumed."""
        disabled = _scalar(
            pg_connection,
            """
            SELECT count(*) FROM pg_constraint
            WHERE conrelid IN ('purchase_order_line'::regclass, 'lifecycle_event'::regclass)
              AND NOT convalidated
            """,
        )
        assert disabled == 0

    def test_the_delivered_constraints_are_present(self, pg_connection) -> None:
        count = _scalar(
            pg_connection,
            """
            SELECT count(*) FROM pg_constraint
            WHERE conrelid IN ('purchase_order_line'::regclass, 'lifecycle_event'::regclass)
              AND contype IN ('c', 'f', 'u', 'p')
            """,
        )
        assert count > 10


class TestDerivedValues:
    def test_lifecycle_state_is_the_last_event_s_state(self, database_url, pg_connection) -> None:
        load(url=str(database_url.render_as_string(hide_password=False)))
        mismatched = _scalar(
            pg_connection,
            """
            SELECT count(*) FROM purchase_order_line p
            WHERE p.lifecycle_state <> (
                SELECT e.to_state FROM lifecycle_event e
                WHERE e.po_line_id = p.po_line_id
                ORDER BY e.sequence_no DESC LIMIT 1
            )
            """,
        )
        assert mismatched == 0

    def test_is_closed_is_the_delivered_biconditional(self, database_url, pg_connection) -> None:
        load(url=str(database_url.render_as_string(hide_password=False)))
        assert (
            _scalar(
                pg_connection,
                """
                SELECT count(*) FROM purchase_order_line
                WHERE is_closed <> (lifecycle_state = 'delivered')
                """,
            )
            == 0
        )

    def test_note_is_null_on_every_event(self, database_url, pg_connection) -> None:
        """DV-022."""
        load(url=str(database_url.render_as_string(hide_password=False)))
        assert (
            _scalar(pg_connection, "SELECT count(*) FROM lifecycle_event WHERE note IS NOT NULL")
            == 0
        )

    def test_from_state_is_null_only_at_sequence_one(self, database_url, pg_connection) -> None:
        load(url=str(database_url.render_as_string(hide_password=False)))
        assert (
            _scalar(
                pg_connection,
                """
                SELECT count(*) FROM lifecycle_event
                WHERE (from_state IS NULL) <> (sequence_no = 1)
                """,
            )
            == 0
        )

    def test_roster_hash_is_one_value_from_the_envelope(
        self, database_url, envelope, pg_connection
    ) -> None:
        """FR-002 at the storage boundary: stamped per row, carried once in the
        envelope rather than repeated 199 times inside the hashed artifact."""
        load(url=str(database_url.render_as_string(hide_password=False)))
        with pg_connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT roster_hash FROM purchase_order_line")
            values = [row[0] for row in cursor.fetchall()]
        assert len(values) == 1
        recorded = next(
            e["digest"]
            for e in envelope["generation_inputs"]
            if e["path"].endswith("project-vendor-roster.json")
        )
        assert values[0] == recorded

    def test_generated_columns_were_populated_by_the_database(
        self, database_url, pg_connection
    ) -> None:
        """The loader names neither, so a non-NULL value proves the database
        wrote them — which is what makes them safe to exclude from the
        comparison projection."""
        load(url=str(database_url.render_as_string(hide_password=False)))
        assert "closing_event_po_line_id" not in LINE_PROJECTION
        assert (
            _scalar(
                pg_connection,
                """
                SELECT count(*) FROM purchase_order_line
                WHERE is_closed AND closing_event_po_line_id IS NULL
                """,
            )
            == 0
        )

    def test_prev_sequence_no_was_generated(self, database_url, pg_connection) -> None:
        load(url=str(database_url.render_as_string(hide_password=False)))
        assert (
            _scalar(
                pg_connection,
                """
                SELECT count(*) FROM lifecycle_event
                WHERE sequence_no > 1 AND prev_sequence_no <> sequence_no - 1
                """,
            )
            == 0
        )


class TestQuantityFidelity:
    def test_quantity_survives_as_a_fixed_scale_decimal(
        self, database_url, envelope, pg_connection
    ) -> None:
        """`numeric` equality ignores trailing zeros — `12.50 = 12.5` in SQL while
        the two are different digests — so the scale is asserted on the stored
        text, not on the numeric comparison."""
        load(url=str(database_url.render_as_string(hide_password=False)))
        with pg_connection.cursor() as cursor:
            cursor.execute("SELECT quantity::text FROM purchase_order_line ORDER BY po_line_id")
            stored = [row[0] for row in cursor.fetchall()]
        assert stored
        for value in stored:
            assert "." in value
            assert len(value.split(".")[1]) == 1


class TestTheEntryPointResolvesItsOwnUrl:
    """The path every test bypassed by supplying a string.

    `get_database_url()` returns a SQLAlchemy `URL`, and the loader called
    `.startswith` on it — an AttributeError the first time `procurement-load`
    ran outside a test. Both forms are exercised here so the entry point and the
    tests take the same route.
    """

    def test_a_url_object_is_accepted(self, database_url, pg_connection) -> None:
        outcome = load(url=database_url)
        assert outcome.lines_inserted > 0

    def test_a_string_is_accepted(self, database_url, pg_connection) -> None:
        outcome = load(url=str(database_url.render_as_string(hide_password=False)))
        assert outcome.lines_inserted > 0

    def test_main_resolves_from_the_environment(self, monkeypatch, database_url) -> None:
        """`main()` takes no url at all — it must resolve one itself."""
        from model.procurement.load import main

        monkeypatch.setenv("DATABASE_URL", database_url.render_as_string(hide_password=False))
        assert main() == 0
