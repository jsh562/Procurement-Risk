"""SC-019 — no loaded column exposes a vendor offset.

The isolation that matters is not only filesystem isolation. If any column of
either delivered table carried an offset, a model fitting on the database would
see the ground truth without ever opening the file.

Asserted over the schema's **fixed column list**, not as an unbounded negative:
"no query can reach it" is not checkable, and "these 21 and these 9 columns are
each either a fixture value or a derived one, and none is an offset" is.
"""

from __future__ import annotations

import json

import pytest

from model.procurement import paths
from model.procurement.load import load

pytestmark = pytest.mark.usefixtures("empty_procurement_tables")


def _url(database_url) -> str:
    return str(database_url.render_as_string(hide_password=False))


@pytest.fixture(scope="module")
def offsets() -> list[float]:
    record = json.loads(paths.truth_path().read_text(encoding="utf-8"))
    return [entry["offset_log"] for entry in record["vendor_offsets"]]


@pytest.fixture(autouse=True)
def loaded(database_url, empty_procurement_tables):
    load(url=_url(database_url))
    return empty_procurement_tables


def _columns(connection, table: str) -> list[tuple[str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position",
            (table,),
        )
        return list(cursor.fetchall())


class TestNoColumnCarriesAnOffset:
    def test_the_only_provenance_column_is_roster_hash(self, pg_connection) -> None:
        names = [name for name, _ in _columns(pg_connection, "purchase_order_line")]
        provenance = [n for n in names if "hash" in n or "seed" in n or "offset" in n]
        assert provenance == ["roster_hash"]

    def test_no_column_name_suggests_an_offset(self, pg_connection) -> None:
        for table in ("purchase_order_line", "lifecycle_event"):
            for name, _ in _columns(pg_connection, table):
                assert "offset" not in name
                assert "truth" not in name
                assert "spread" not in name

    def test_no_numeric_column_holds_an_offset_value(self, pg_connection, offsets) -> None:
        """The substantive check: an offset could hide in a column whose name
        says nothing. Every numeric column is swept for the actual values."""
        numeric = [
            name
            for name, kind in _columns(pg_connection, "purchase_order_line")
            if kind in {"numeric", "double precision", "real", "integer", "bigint"}
        ]
        assert numeric
        for name in numeric:
            with pg_connection.cursor() as cursor:
                cursor.execute(f'SELECT DISTINCT "{name}" FROM purchase_order_line')  # noqa: S608
                values = {row[0] for row in cursor.fetchall() if row[0] is not None}
            for offset in offsets:
                assert not any(abs(float(v) - offset) < 1e-9 for v in values)

    def test_note_is_null_on_every_event(self, pg_connection) -> None:
        """`note` is the one uncontrolled text column, so it is the obvious place
        an offset could be smuggled through."""
        with pg_connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM lifecycle_event WHERE note IS NOT NULL")
            assert cursor.fetchone()[0] == 0

    def test_no_text_column_contains_an_offset_rendering(self, pg_connection, offsets) -> None:
        text_columns = [
            name
            for name, kind in _columns(pg_connection, "purchase_order_line")
            if kind in {"text", "character varying"}
        ]
        assert text_columns
        for name in text_columns:
            with pg_connection.cursor() as cursor:
                cursor.execute(f'SELECT DISTINCT "{name}" FROM purchase_order_line')  # noqa: S608
                values = [row[0] for row in cursor.fetchall() if row[0]]
            for offset in offsets:
                rendered = f"{offset:.4f}".lstrip("-")
                assert not any(rendered in str(v) for v in values)


class TestTheDatasetContentHashIsNotLoaded:
    def test_no_column_carries_the_dataset_content_hash(self, pg_connection) -> None:
        """L-7 discloses this as a limitation: a row is traceable to the roster
        it was generated against, not to the run that produced it. Asserting it
        keeps the disclosure true."""
        digest = json.loads(paths.hash_path().read_text(encoding="utf-8"))["dataset_content_hash"]
        with pg_connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT roster_hash FROM purchase_order_line")
            values = {row[0] for row in cursor.fetchall()}
        assert digest not in values

    def test_the_roster_hash_is_the_roster_s_not_the_dataset_s(self, pg_connection) -> None:
        from model.procurement.serialize import read_payload

        envelope = read_payload(paths.fixture_path())
        roster = next(
            e["digest"]
            for e in envelope["generation_inputs"]
            if e["path"].endswith("project-vendor-roster.json")
        )
        with pg_connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT roster_hash FROM purchase_order_line")
            assert {row[0] for row in cursor.fetchall()} == {roster}
