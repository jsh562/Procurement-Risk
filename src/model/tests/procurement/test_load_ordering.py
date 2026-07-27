"""SC-030 — the chain foreign key is satisfied at every statement, not at commit.

`fk_lifecycle_event__chain` is **not** deferrable. Event N references event N−1 of
the same line, so the insert must be ordered ascending by
`(po_line_id, sequence_no)` and every intermediate state must already be legal.
Only `fk_purchase_order_line__closing_event` gets to wait for `COMMIT`.
"""

from __future__ import annotations

import psycopg
import pytest

from model.procurement.load import load

pytestmark = pytest.mark.usefixtures("empty_procurement_tables")


def _url(database_url) -> str:
    return str(database_url.render_as_string(hide_password=False))


def _one(connection, sql: str):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchone()[0]


class TestTheChainConstraintIsNonDeferrable:
    def test_the_chain_foreign_key_is_not_deferrable(self, pg_connection) -> None:
        deferrable = _one(
            pg_connection,
            """
            SELECT condeferrable FROM pg_constraint
            WHERE conname = 'fk_lifecycle_event__chain'
            """,
        )
        assert deferrable is False

    def test_out_of_order_insertion_is_rejected_immediately(
        self, database_url, pg_connection
    ) -> None:
        """The behaviour that forces the loader's ordering.

        Inserting sequence 3 before sequence 2 must fail at the *statement*, not
        at commit — which is what makes ascending insertion a requirement rather
        than a preference.
        """
        load(url=_url(database_url))
        line = _one(
            pg_connection,
            """
            SELECT po_line_id FROM purchase_order_line
            ORDER BY project_id, po_number, line_number LIMIT 1
            """,
        )
        with pg_connection.cursor() as cursor:
            cursor.execute("DELETE FROM lifecycle_event WHERE po_line_id = %s", (line,))
            cursor.execute(
                "UPDATE purchase_order_line SET closing_event_id = NULL, is_closed = false, "
                "lifecycle_state = 'submitted' WHERE po_line_id = %s",
                (line,),
            )
        pg_connection.commit()

        with pytest.raises(psycopg.errors.ForeignKeyViolation), pg_connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO lifecycle_event
                    (event_id, po_line_id, sequence_no, from_state, to_state,
                     is_terminal, occurred_at, note)
                VALUES (gen_random_uuid(), %s, 3, 'under_review', 'approved',
                        false, now(), NULL)
                """,
                (line,),
            )
        pg_connection.rollback()


class TestTheLoadInsertsInOrder:
    def test_events_are_present_in_contiguous_ascending_order(
        self, database_url, pg_connection
    ) -> None:
        load(url=_url(database_url))
        broken = _one(
            pg_connection,
            """
            SELECT count(*) FROM (
                SELECT po_line_id,
                       sequence_no,
                       row_number() OVER (PARTITION BY po_line_id ORDER BY sequence_no) AS rn
                FROM lifecycle_event
            ) t WHERE sequence_no <> rn
            """,
        )
        assert broken == 0

    def test_every_chain_starts_at_one(self, database_url, pg_connection) -> None:
        load(url=_url(database_url))
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM (
                    SELECT po_line_id, min(sequence_no) AS lo FROM lifecycle_event
                    GROUP BY po_line_id
                ) t WHERE lo <> 1
                """,
            )
            == 0
        )

    def test_prev_sequence_no_chains_correctly(self, database_url, pg_connection) -> None:
        """`GENERATED ALWAYS` from `sequence_no`, so this checks the database's
        own derivation rather than the loader's."""
        load(url=_url(database_url))
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM lifecycle_event
                WHERE (prev_sequence_no IS NULL) <> (sequence_no = 1)
                """,
            )
            == 0
        )

    def test_every_adjacent_pair_is_a_legal_transition(self, database_url, pg_connection) -> None:
        """Enforced by the delivered `fn_is_legal_lifecycle_transition`, so this
        confirms the loader's `from_state` derivation agrees with it."""
        load(url=_url(database_url))
        assert (
            _one(
                pg_connection,
                """
                SELECT count(*) FROM lifecycle_event e
                WHERE e.from_state IS NOT NULL
                  AND NOT fn_is_legal_lifecycle_transition(e.from_state, e.to_state)
                """,
            )
            == 0
        )

    def test_the_loader_orders_its_event_insert(self) -> None:
        """A source-level check: the SQL must carry the ORDER BY, because a
        correct result under one plan does not prove the ordering was requested."""
        from pathlib import Path

        import model.procurement.load as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        insert = source[source.index("INSERT INTO lifecycle_event") :]
        assert "ORDER BY po_line_id, sequence_no" in insert.split(")")[0] or (
            "ORDER BY po_line_id, sequence_no" in insert[:800]
        )
