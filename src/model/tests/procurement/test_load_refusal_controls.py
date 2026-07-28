"""NC-9 and NC-4's integration half — both refusals leave the database unchanged.

DV-027 is the part worth stating: a refusal that had already inserted some rows
before deciding to refuse would leave a half-loaded database *and* report an
error, which is the state a later run can mistake for a completed one. The
transaction is what prevents it, and "unchanged" is two observations of the same
count, not one.
"""

from __future__ import annotations

import json

import pytest

from model.procurement import paths
from model.procurement.load import LoadError, load
from model.procurement.serialize import read_payload, write_payload
from model.roster.reader import ROSTER_FILENAME

pytestmark = pytest.mark.usefixtures("empty_procurement_tables")


def _url(database_url) -> str:
    return str(database_url.render_as_string(hide_password=False))


@pytest.fixture
def divergent_fixture(tmp_path):
    """The committed fixture with exactly one compared field changed."""
    envelope = read_payload(paths.fixture_path())
    envelope["lines"][7]["description"] = "Deliberately Divergent — control"
    target = tmp_path / "divergent.json"
    write_payload(target, envelope)
    return target


@pytest.fixture
def subset_fixture(tmp_path):
    """The committed fixture with lines removed, so the database is a superset."""
    envelope = read_payload(paths.fixture_path())
    envelope["lines"] = envelope["lines"][:150]
    target = tmp_path / "subset.json"
    write_payload(target, envelope)
    return target


class TestDivergenceRefusal:
    def test_a_divergent_line_refuses(self, database_url, divergent_fixture) -> None:
        load(url=_url(database_url))
        with pytest.raises(LoadError, match="differ on a compared field"):
            load(fixture=divergent_fixture, url=_url(database_url))

    def test_the_refusal_names_the_key(self, database_url, divergent_fixture) -> None:
        load(url=_url(database_url))
        with pytest.raises(LoadError) as raised:
            load(fixture=divergent_fixture, url=_url(database_url))
        envelope = read_payload(divergent_fixture)
        assert envelope["lines"][7]["po_number"] in str(raised.value)

    def test_the_database_is_unchanged_after_the_refusal(
        self, database_url, divergent_fixture, row_counts, pg_connection
    ) -> None:
        load(url=_url(database_url))
        before = row_counts()
        with pytest.raises(LoadError):
            load(fixture=divergent_fixture, url=_url(database_url))
        pg_connection.rollback()
        assert row_counts() == before

    def test_no_content_changed_after_the_refusal(
        self, database_url, divergent_fixture, pg_connection
    ) -> None:
        """A refusal must not have applied the divergent value first."""
        load(url=_url(database_url))
        with pytest.raises(LoadError):
            load(fixture=divergent_fixture, url=_url(database_url))
        pg_connection.rollback()
        with pg_connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM purchase_order_line WHERE description LIKE '%Divergent%'"
            )
            assert cursor.fetchone()[0] == 0


class TestSupersetRefusal:
    def test_a_database_superset_refuses(self, database_url, subset_fixture) -> None:
        """FR-030: the database holds keys the fixture does not."""
        load(url=_url(database_url))
        with pytest.raises(LoadError, match="the fixture does not contain"):
            load(fixture=subset_fixture, url=_url(database_url))

    def test_the_refusal_names_the_extra_keys(self, database_url, subset_fixture) -> None:
        load(url=_url(database_url))
        with pytest.raises(LoadError) as raised:
            load(fixture=subset_fixture, url=_url(database_url))
        assert "PRJ-" in str(raised.value)

    def test_the_database_is_unchanged(
        self, database_url, subset_fixture, row_counts, pg_connection
    ) -> None:
        load(url=_url(database_url))
        before = row_counts()
        with pytest.raises(LoadError):
            load(fixture=subset_fixture, url=_url(database_url))
        pg_connection.rollback()
        assert row_counts() == before

    def test_nothing_was_deleted(self, database_url, subset_fixture, pg_connection) -> None:
        """Refusing rather than deleting is the point — FR-030 forbids the merge."""
        load(url=_url(database_url))
        with pytest.raises(LoadError):
            load(fixture=subset_fixture, url=_url(database_url))
        pg_connection.rollback()
        with pg_connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM purchase_order_line")
            assert cursor.fetchone()[0] == len(read_payload(paths.fixture_path())["lines"])


class TestRefusalBeforeAnyInsert:
    def test_a_refusal_on_an_empty_database_inserts_nothing(
        self, database_url, tmp_path, row_counts, pg_connection
    ) -> None:
        """The strongest form of DV-027: the refusal fires on the *first* load,
        so any row present afterwards was written by the run that refused."""
        envelope = read_payload(paths.fixture_path())
        envelope["generation_inputs"][0]["digest"] = "sha256:" + "0" * 64
        target = tmp_path / "drifted.json"
        write_payload(target, envelope)

        assert row_counts() == (0, 0)
        with pytest.raises(LoadError, match="drifted"):
            load(fixture=target, url=_url(database_url))
        pg_connection.rollback()
        assert row_counts() == (0, 0)


class TestInputDriftRefusal:
    """NC-4's integration half: each named input refuses the load."""

    @pytest.mark.parametrize(
        "path_fragment",
        [ROSTER_FILENAME, "equipment-category-map.json", "manufacturer-catalog.json"],
    )
    def test_each_drifted_input_refuses_and_names_itself(
        self, database_url, tmp_path, path_fragment, row_counts, pg_connection
    ) -> None:
        envelope = read_payload(paths.fixture_path())
        for entry in envelope["generation_inputs"]:
            if entry["path"].endswith(path_fragment):
                entry["digest"] = "sha256:" + "1" * 64
        target = tmp_path / f"drift-{path_fragment}"
        write_payload(target, envelope)

        before = row_counts()
        with pytest.raises(LoadError) as raised:
            load(fixture=target, url=_url(database_url))
        assert path_fragment in str(raised.value)
        pg_connection.rollback()
        assert row_counts() == before

    def test_all_three_inputs_are_actually_checked(self) -> None:
        """A drift check covering two of three inputs would pass every test above
        that happens to mutate one of the two."""
        envelope = read_payload(paths.fixture_path())
        assert len(envelope["generation_inputs"]) == 3

    def test_an_unknown_recorded_input_refuses(self, database_url, tmp_path) -> None:
        """An input this loader cannot recompute is unverifiable provenance,
        which is worse than an absent one."""
        envelope = read_payload(paths.fixture_path())
        envelope["generation_inputs"].append(
            {
                "path": "data/corpus/synthetic/not-a-real-input.json",
                "digest": "sha256:" + "2" * 64,
                "digest_kind": "raw_bytes",
            }
        )
        target = tmp_path / "unknown-input.json"
        write_payload(target, envelope)
        with pytest.raises(LoadError, match="does not know how to recompute"):
            load(fixture=target, url=_url(database_url))


def test_the_committed_fixture_still_loads_cleanly(database_url, row_counts) -> None:
    """A control on the controls: every refusal above is about a mutated input,
    so the unmutated one must still succeed."""
    outcome = load(url=_url(database_url))
    assert outcome.lines_inserted > 0
    assert row_counts()[0] == outcome.lines_inserted
    _ = json
