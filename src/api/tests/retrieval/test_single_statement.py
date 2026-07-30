"""One search executes exactly one ranking statement, and no `SET`.

Spec FR-002. Asserted over what the connection actually executes, not over the
statement text — `test_fusion_plan_shape.py` does the cheap structural half, and
a text assertion cannot see a second statement issued from somewhere else in the
call path. That is the failure worth catching: nothing about the ranking SQL
changes when a caller adds `SET hnsw.ef_search = …` before it, which is the
obvious way to make the breadth take effect and is exactly what AD-002 forbids.

The search breadth rides on the **connection** instead
(`db.connection_options`), applied by the backend at session start, so the
ranking query arrives with it already in force. `specs/00003-core-data-schema`
prescribes setting it "at query time", which as written would be that second
statement; AD-002 declares this plan normative over that line.

Statements are captured by wrapping the cursor rather than by parsing a log:
a log-based check depends on `log_statement` being on and would pass silently
on a server where it is not.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest

from api.config import load_retrieval_config
from api.db import connection_options
from api.retrieval.fusion import FUSION_SQL, retrieval_parameters


#: Statements captured by the cursor below. Module-level because psycopg 3 sets
#: the factory on the *connection* and constructs cursors itself, so there is no
#: seam to inject a per-test list through.
_EXECUTED: list[str] = []


class _RecordingCursor(psycopg.Cursor):
    """A cursor that records every statement executed through it.

    Wrapping the cursor rather than reading a server log: a log-based check
    depends on `log_statement` being enabled and would pass silently on a server
    where it is not — which is the same vacuity this file exists to prevent.
    """

    def execute(self, query, params=None, **kwargs):  # type: ignore[no-untyped-def]
        _EXECUTED.append(str(query))
        return super().execute(query, params, **kwargs)


@pytest.fixture
def connection() -> Iterator[psycopg.Connection]:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is unset; statement capture needs a real connection")
    config = load_retrieval_config({})
    with psycopg.connect(url, options=connection_options(config)) as conn:
        yield conn
        conn.rollback()


def _run_one_search(connection: psycopg.Connection) -> list[str]:
    """Execute one search and return every statement it issued."""
    _EXECUTED.clear()
    previous = connection.cursor_factory
    connection.cursor_factory = _RecordingCursor
    try:
        with connection.cursor() as cursor:
            config = load_retrieval_config({})
            cursor.execute(
                FUSION_SQL, retrieval_parameters("bronze valve", [0.0] * 384, config=config)
            )
            cursor.fetchall()
    finally:
        connection.cursor_factory = previous
    return list(_EXECUTED)


def test_one_search_issues_exactly_one_statement(connection: psycopg.Connection) -> None:
    """The count is one. Not "one ranking statement plus some setup"."""
    executed = _run_one_search(connection)
    assert len(executed) == 1, f"one search issued {len(executed)} statements:\n" + "\n".join(
        f"  {statement.strip()[:90]}" for statement in executed
    )


def test_no_statement_a_search_issues_is_a_set(connection: psycopg.Connection) -> None:
    """No `SET` anywhere on the search path.

    Separate from the count because they fail for different reasons and a
    reader should be told which. A `SET` issued *instead of* connection options
    would keep the count at two and this names it; a `SET` folded into the
    ranking statement would keep the count at one and this names that too.
    """
    executed = _run_one_search(connection)
    offenders = [s for s in executed if "set " in s.lower()]
    assert not offenders, (
        f"a search issued a SET, which is a second statement under FR-002 and is "
        f"what connection options exist to avoid: {offenders}"
    )


def test_the_breadth_is_in_force_without_the_search_setting_it(
    connection: psycopg.Connection,
) -> None:
    """The connection carries the breadth, so the query need not.

    This is the half that makes the one-statement rule *possible* rather than
    merely asserted. If the setting were not already applied, the only ways to
    honour FR-027 would be a second statement or nothing at all.
    """
    config = load_retrieval_config({})
    with connection.cursor() as cursor:
        cursor.execute("SHOW hnsw.ef_search")
        row = cursor.fetchone()
    assert row is not None
    assert int(row[0]) == config.search_breadth, (
        f"the connection carries ef_search={row[0]}, not the configured "
        f"{config.search_breadth}; the breadth is not in force at session start"
    )


def test_the_ranking_statement_carries_no_separator() -> None:
    """A semicolon would make one `execute` two statements.

    psycopg sends the string as-is, so an accidental separator is a second
    statement the count above would not see — it is one `execute` call.
    """
    assert ";" not in FUSION_SQL
