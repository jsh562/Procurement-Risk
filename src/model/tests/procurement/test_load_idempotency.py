"""SC-009 — a reload changes no count and no content over the stated projection.

Row counts are deliberately *not* the comparison on their own: a regeneration
under the same seed policy produces the same natural keys with different content,
which moves no count at all. Both are asserted.
"""

from __future__ import annotations

import pytest

from model.procurement.load import EVENT_PROJECTION, LINE_PROJECTION, load

pytestmark = pytest.mark.usefixtures("empty_procurement_tables")


def _url(database_url) -> str:
    return str(database_url.render_as_string(hide_password=False))


#: Assembled once from the closed projections, not per call: the values are
#: column names this package owns, and hoisting them keeps the snapshot and the
#: loader's own comparison reading from one definition.
SELECT_LINES = (
    f"SELECT {', '.join(LINE_PROJECTION)} FROM purchase_order_line "  # noqa: S608
    f"ORDER BY project_id, po_number, line_number"
)
SELECT_EVENTS = (
    f"SELECT po_line_id, {', '.join(EVENT_PROJECTION)} FROM lifecycle_event "  # noqa: S608
    f"ORDER BY po_line_id, sequence_no"
)


def _snapshot(connection) -> tuple[list, list]:
    with connection.cursor() as cursor:
        cursor.execute(SELECT_LINES)
        lines = cursor.fetchall()
        cursor.execute(SELECT_EVENTS)
        events = cursor.fetchall()
    return lines, events


class TestReload:
    def test_a_second_load_inserts_nothing(self, database_url, pg_connection) -> None:
        first = load(url=_url(database_url))
        second = load(url=_url(database_url))
        assert first.lines_inserted > 0
        assert second.lines_inserted == 0
        assert second.events_inserted == 0
        assert second.lines_skipped == first.lines_inserted

    def test_content_is_unchanged_over_the_projection(self, database_url, pg_connection) -> None:
        load(url=_url(database_url))
        before = _snapshot(pg_connection)
        load(url=_url(database_url))
        assert _snapshot(pg_connection) == before

    def test_counts_are_unchanged(self, database_url, pg_connection, row_counts) -> None:
        load(url=_url(database_url))
        before = row_counts()
        load(url=_url(database_url))
        assert row_counts() == before

    def test_a_third_load_is_still_a_no_op(self, database_url, pg_connection) -> None:
        """Idempotency is not "the second one is safe" — it is stable."""
        load(url=_url(database_url))
        before = _snapshot(pg_connection)
        load(url=_url(database_url))
        load(url=_url(database_url))
        assert _snapshot(pg_connection) == before


class TestCreatedAtIsExcluded:
    def test_created_at_is_not_in_the_projection(self) -> None:
        """DV-022. `DEFAULT now()` differs on every load by construction, so
        comparing it would make a reload of identical content register as
        divergence."""
        assert "created_at" not in LINE_PROJECTION

    def test_created_at_does_not_change_on_reload(self, database_url, pg_connection) -> None:
        """It is excluded from the comparison *and* untouched, because the second
        load issues no statement against an already-present row."""
        load(url=_url(database_url))
        with pg_connection.cursor() as cursor:
            cursor.execute("SELECT max(created_at) FROM purchase_order_line")
            before = cursor.fetchone()[0]
        load(url=_url(database_url))
        with pg_connection.cursor() as cursor:
            cursor.execute("SELECT max(created_at) FROM purchase_order_line")
            assert cursor.fetchone()[0] == before

    def test_the_generated_columns_are_not_in_the_projection(self) -> None:
        for column in ("closing_event_po_line_id", "closing_event_terminal"):
            assert column not in LINE_PROJECTION

    def test_prev_sequence_no_is_not_in_the_event_projection(self) -> None:
        assert "prev_sequence_no" not in EVENT_PROJECTION

    def test_note_is_in_the_event_projection(self) -> None:
        """Deliberately compared, not excluded: leaving the one uncontrolled text
        column out would give a divergent generation a place to differ silently."""
        assert "note" in EVENT_PROJECTION
