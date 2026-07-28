"""`EXCEPT`'s not-distinct NULL semantics, in both directions (T045).

This is the property that lets the reconciliation carry no `IS NULL` anywhere.
`=` treats `NULL = NULL` as unknown, so a comparison built on `=` would report
every open line as divergent — `closing_event_id` is NULL on all of them. `EXCEPT`
compares rows with *not-distinct* semantics, so two NULLs match.

Three columns depend on it: `closing_event_id` (NULL on every open line),
`from_state` (NULL at sequence 1 of every chain) and `note` (NULL on every E005
event). Between them they cover a majority of rows, so getting this wrong would
not be a rare edge case — it would refuse every reload.
"""

from __future__ import annotations

import pytest

from model.procurement.load import load

pytestmark = pytest.mark.usefixtures("empty_procurement_tables")


def _url(database_url) -> str:
    return str(database_url.render_as_string(hide_password=False))


def _one(connection, sql: str):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    return row[0]


class TestTheSemanticsThemselves:
    """Asserted against the server, not assumed from the documentation."""

    def test_except_treats_two_nulls_as_equal(self, pg_connection) -> None:
        assert _one(pg_connection, "SELECT count(*) FROM (SELECT NULL EXCEPT SELECT NULL) x") == 0

    def test_equality_does_not(self, pg_connection) -> None:
        assert _one(pg_connection, "SELECT (NULL = NULL) IS NULL") is True

    def test_except_all_preserves_multiplicity(self, pg_connection) -> None:
        """`EXCEPT ALL`, not `EXCEPT`: plain `EXCEPT` deduplicates, which would
        hide a fixture carrying a row twice."""
        rows = _one(
            pg_connection,
            """
            SELECT count(*) FROM (
                (SELECT 1 UNION ALL SELECT 1) EXCEPT ALL (SELECT 1)
            ) x
            """,
        )
        assert rows == 1

    def test_except_distinguishes_null_from_a_value(self, pg_connection) -> None:
        assert _one(pg_connection, "SELECT count(*) FROM (SELECT NULL EXCEPT SELECT 1) x") == 1


class TestTheThreeNullableColumnsInPractice:
    def test_open_lines_carry_a_null_closing_event(self, database_url, pg_connection) -> None:
        load(url=_url(database_url))
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM purchase_order_line
                WHERE (closing_event_id IS NULL) <> (NOT is_closed)
                """,
            )
            == 0
        )
        assert (
            _one(
                pg_connection,
                "SELECT count(*) FROM purchase_order_line WHERE closing_event_id IS NULL",
            )
            > 0
        )

    def test_from_state_is_null_at_every_chain_start(self, database_url, pg_connection) -> None:
        load(url=_url(database_url))
        assert (
            _one(
                pg_connection,
                "SELECT count(*) FROM lifecycle_event "
                "WHERE sequence_no = 1 AND from_state IS NOT NULL",
            )
            == 0
        )

    def test_note_is_null_everywhere(self, database_url, pg_connection) -> None:
        load(url=_url(database_url))
        assert (
            _one(pg_connection, "SELECT count(*) FROM lifecycle_event WHERE note IS NOT NULL") == 0
        )

    def test_a_reload_does_not_refuse_despite_all_those_nulls(
        self, database_url, pg_connection
    ) -> None:
        """The whole point. A reconciliation built on `=` would refuse here,
        because every open line and every first event carries a NULL."""
        load(url=_url(database_url))
        second = load(url=_url(database_url))
        assert second.lines_inserted == 0
        assert second.lines_skipped > 0


class TestBothDirections:
    def test_a_null_becoming_a_value_is_detected(self, database_url, pg_connection) -> None:
        """Fixture NULL, database value — one direction of the `EXCEPT ALL` pair."""
        load(url=_url(database_url))
        with pg_connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE lifecycle_event SET note = 'planted'
                WHERE sequence_no = 1
                  AND po_line_id = (SELECT po_line_id FROM purchase_order_line
                                    ORDER BY project_id, po_number, line_number LIMIT 1)
                """
            )
        pg_connection.commit()
        from model.procurement.load import LoadError

        with pytest.raises(LoadError, match="diverge"):
            load(url=_url(database_url))
        pg_connection.rollback()

    def test_the_reconciliation_queries_both_directions(self) -> None:
        """The symmetric half, asserted structurally rather than behaviourally.

        Planting the database-NULL/fixture-value case is not possible from
        outside: `ck_lifecycle_event__first_has_no_predecessor` rejects a NULL
        `from_state` at sequence 2, and the closure CHECKs reject a NULL
        `closing_event_id` on a closed line. The schema forbids constructing the
        state, which is a stronger guarantee than a test of it would be — so what
        remains to verify is that the query would catch it, and that is visible
        in the SQL.
        """
        from pathlib import Path

        import model.procurement.load as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert source.count("EXCEPT ALL") >= 4
        for direction in (
            "SELECT * FROM db EXCEPT ALL SELECT * FROM fx",
            "SELECT * FROM fx EXCEPT ALL SELECT * FROM db",
        ):
            assert direction in source
        assert "IS NULL" not in source.split("def _reconcile")[1].split("def _insert")[0]
