"""SC-011 — closure, and the one `DEFERRABLE INITIALLY DEFERRED` foreign key.

`fk_purchase_order_line__closing_event` validates only at `COMMIT`. That is why
this tier cannot wrap each test in a rolled-back transaction the way the schema
tier does: a harness that never reached a commit would let a closed line naming a
nonexistent event pass, which is the one thing this file exists to catch.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from model.procurement.load import load

pytestmark = pytest.mark.usefixtures("empty_procurement_tables")


def _url(database_url) -> str:
    return str(database_url.render_as_string(hide_password=False))


def _one(connection, sql: str, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    return row[0]


class TestClosureInvariant:
    def test_terminal_lines_are_closed_and_name_their_event(
        self, database_url, pg_connection
    ) -> None:
        load(url=_url(database_url))
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM purchase_order_line
                WHERE lifecycle_state = 'delivered'
                  AND (NOT is_closed OR closing_event_id IS NULL)
                """,
            )
            == 0
        )

    def test_open_lines_are_not_closed_and_name_nothing(self, database_url, pg_connection) -> None:
        load(url=_url(database_url))
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM purchase_order_line
                WHERE lifecycle_state <> 'delivered'
                  AND (is_closed OR closing_event_id IS NOT NULL)
                """,
            )
            == 0
        )

    def test_the_closing_event_is_the_line_s_own_terminal_event(
        self, database_url, pg_connection
    ) -> None:
        load(url=_url(database_url))
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM purchase_order_line p
                JOIN lifecycle_event e ON e.event_id = p.closing_event_id
                WHERE p.is_closed
                  AND (e.po_line_id <> p.po_line_id OR NOT e.is_terminal)
                """,
            )
            == 0
        )

    def test_both_populations_are_non_empty(self, database_url, pg_connection) -> None:
        """Otherwise one of the two assertions above is vacuous."""
        load(url=_url(database_url))
        assert _one(pg_connection, "SELECT count(*) FROM purchase_order_line WHERE is_closed") > 0
        assert (
            _one(pg_connection, "SELECT count(*) FROM purchase_order_line WHERE NOT is_closed") > 0
        )

    def test_open_lines_are_reachable_through_the_partial_index(
        self, database_url, pg_connection
    ) -> None:
        """`ix_purchase_order_line__open` is the read surface this epic populates
        for E007; assert it exists and covers the open set."""
        load(url=_url(database_url))
        assert (
            _one(
                pg_connection,
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_purchase_order_line__open'",
            )
            == 1
        )


class TestTheDeferredConstraintIsRealAndValidatesAtCommit:
    def test_the_constraint_is_deferrable_initially_deferred(self, pg_connection) -> None:
        row = _one(
            pg_connection,
            """
            SELECT condeferrable AND condeferred FROM pg_constraint
            WHERE conname = 'fk_purchase_order_line__closing_event'
            """,
        )
        assert row is True

    def test_it_is_the_only_deferred_constraint_on_these_tables(self, pg_connection) -> None:
        """One deferrable foreign key, deliberately. If a second appeared, the
        ordering guarantees the loader relies on would no longer be stated."""
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM pg_constraint
                WHERE conrelid IN ('purchase_order_line'::regclass, 'lifecycle_event'::regclass)
                  AND condeferred
                """,
            )
            == 1
        )

    def test_a_dangling_closing_event_is_rejected_at_commit_not_at_statement(
        self, database_url, pg_connection
    ) -> None:
        """The behaviour the whole harness design turns on.

        Setting `closing_event_id` to a nonexistent uuid must *succeed* as a
        statement and fail at `COMMIT`. A test tier that rolled back instead of
        committing would never observe the failure.
        """
        load(url=_url(database_url))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with pg_connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE purchase_order_line SET closing_event_id = %s
                    WHERE po_line_id = (
                        SELECT po_line_id FROM purchase_order_line WHERE is_closed
                        ORDER BY project_id, po_number, line_number LIMIT 1
                    )
                    """,
                    (str(uuid.uuid5(uuid.NAMESPACE_URL, "no-such-event")),),
                )
            # The statement above is accepted; the violation surfaces here.
            pg_connection.commit()
        pg_connection.rollback()

    def test_the_load_commits_rather_than_rolling_back(
        self, database_url, pg_connection, row_counts
    ) -> None:
        """If the loader never committed, the deferred constraint would never be
        validated and the closure invariant would be untested."""
        load(url=_url(database_url))
        assert row_counts()[0] > 0
