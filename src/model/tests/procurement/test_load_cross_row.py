"""SC-031 / DV-003 / DV-008 — the cross-row invariants, checked in the database.

DV-003's one-project-one-vendor-per-order clause is marked **generator-only
(G-2)**: the delivered schema does not enforce it. So this file is the only place
it is checked against loaded rows, and a regression would otherwise reach a
consumer.
"""

from __future__ import annotations

import pytest

from model.procurement.censor import AS_OF_DATE
from model.procurement.load import load

pytestmark = pytest.mark.usefixtures("empty_procurement_tables")


#: The as-of date is a committed constant, so this interpolates a date literal
#: rather than a value from anywhere a caller controls.
NO_INSTANT_PAST_AS_OF = (
    f"SELECT count(*) FROM lifecycle_event "  # noqa: S608
    f"WHERE occurred_at::date > DATE '{AS_OF_DATE.isoformat()}'"
)


def _url(database_url) -> str:
    return str(database_url.render_as_string(hide_password=False))


def _one(connection, sql: str):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchone()[0]


@pytest.fixture(autouse=True)
def loaded(database_url, empty_procurement_tables):
    load(url=_url(database_url))
    return empty_procurement_tables


class TestDV003:
    def test_each_order_carries_one_project_and_one_vendor(self, pg_connection) -> None:
        """Generator-only under G-2 — nothing in the schema rejects this."""
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM (
                    SELECT project_id, po_number
                    FROM purchase_order_line
                    GROUP BY project_id, po_number
                    HAVING count(DISTINCT vendor_id) > 1
                ) t
                """,
            )
            == 0
        )

    def test_natural_keys_are_unique(self, pg_connection) -> None:
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM (
                    SELECT project_id, po_number, line_number
                    FROM purchase_order_line
                    GROUP BY project_id, po_number, line_number
                    HAVING count(*) > 1
                ) t
                """,
            )
            == 0
        )

    def test_line_numbers_are_contiguous_within_each_order(self, pg_connection) -> None:
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM (
                    SELECT project_id, po_number,
                           count(*) AS n, min(line_number) AS lo, max(line_number) AS hi
                    FROM purchase_order_line
                    GROUP BY project_id, po_number
                ) t WHERE lo <> 1 OR hi <> n
                """,
            )
            == 0
        )

    def test_multi_line_orders_exist(self, pg_connection) -> None:
        """Otherwise the contiguity assertion above is about one-line orders only."""
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM (
                    SELECT project_id, po_number FROM purchase_order_line
                    GROUP BY project_id, po_number HAVING count(*) > 1
                ) t
                """,
            )
            > 0
        )


class TestDV008:
    def test_occurred_at_strictly_increases_with_sequence_no(self, pg_connection) -> None:
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM (
                    SELECT po_line_id, occurred_at,
                           lag(occurred_at) OVER (
                               PARTITION BY po_line_id ORDER BY sequence_no
                           ) AS prev
                    FROM lifecycle_event
                ) t WHERE prev IS NOT NULL AND occurred_at <= prev
                """,
            )
            == 0
        )

    def test_event_one_is_dated_the_order_date(self, pg_connection) -> None:
        """The opening transition *is* the clock start."""
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM purchase_order_line p
                JOIN lifecycle_event e
                  ON e.po_line_id = p.po_line_id AND e.sequence_no = 1
                WHERE e.occurred_at::date <> p.order_date
                """,
            )
            == 0
        )

    def test_no_instant_exceeds_the_as_of_date(self, pg_connection) -> None:
        assert (
            _one(
                pg_connection,
                NO_INSTANT_PAST_AS_OF,
            )
            == 0
        )

    def test_every_instant_is_midnight_utc(self, pg_connection) -> None:
        """Durations are whole days, so anything but midnight is invented precision."""
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM lifecycle_event
                WHERE occurred_at <> date_trunc('day', occurred_at)
                """,
            )
            == 0
        )

    def test_need_by_is_never_before_the_order_date(self, pg_connection) -> None:
        assert (
            _one(
                pg_connection,
                "SELECT count(*) FROM purchase_order_line WHERE need_by_date < order_date",
            )
            == 0
        )


class TestCoverage:
    def test_all_five_projects_and_twelve_vendors_are_present(self, pg_connection) -> None:
        assert (
            _one(pg_connection, "SELECT count(DISTINCT project_id) FROM purchase_order_line") == 5
        )
        assert (
            _one(pg_connection, "SELECT count(DISTINCT vendor_id) FROM purchase_order_line") == 12
        )

    def test_all_five_criticality_bands_occur(self, pg_connection) -> None:
        assert (
            _one(pg_connection, "SELECT count(DISTINCT criticality) FROM purchase_order_line") == 5
        )

    def test_every_non_terminal_state_holds_at_least_one_line(self, pg_connection) -> None:
        """FR-010's hard failure, verified on the loaded data rather than only in
        the generator's own gate."""
        missing = _one(
            pg_connection,
            """
            SELECT count(*) FROM (
                SELECT s.state FROM (VALUES
                    ('submitted'), ('under_review'), ('approved'), ('revise_and_resubmit'),
                    ('released_for_fabrication'), ('shipped')
                ) AS s(state)
                WHERE NOT EXISTS (
                    SELECT 1 FROM purchase_order_line p WHERE p.lifecycle_state = s.state
                )
            ) t
            """,
        )
        assert missing == 0
