"""T091 — NC-18 / SC-037 / FR-036: `0300` against a populated `forecast_run`.

`0300` adds fourteen `NOT NULL` columns with no default, which PostgreSQL
refuses on a populated table — and a default is unavailable, because the
delivered TR-063 audit admits defaults on an enumerated six columns and none of
the fourteen is one of them. So the precondition is real and unavoidable. What
FR-036 requires is that the migration says so **itself**, with its own named
error, rather than letting the server report a not-null violation on a column
that has existed for three milliseconds: an operator reading that names the
symptom, and has to reconstruct which of the fourteen statements failed and why
a NULL appeared in a column nobody wrote to.

Both directions, because each fails on its own. Against a **populated** table the
migration raises `ForecastRunNotEmptyError` and leaves the schema exactly as it
found it — the guard runs before any DDL is issued, so a refusal does not rely
on the transaction to undo half of it. Against an **empty** one it applies
cleanly, which is the half that stops "always refuse" from passing this file.

**A scratch database of its own, per test.** The shared database is at head, so
`0300` has already run on it and neither direction is reachable there; and the
populated case has to *commit* a run row for the migration's own connection to
see it, which this tier's rolled-back session is designed to prevent. The shape
is `tests/schema/test_migration_chain.py::scratch_database`, re-derived here
rather than imported: the two tiers are separate pytest rootdirs on `sys.path`
and neither is the other's package.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.pool import NullPool

from model.schema.cli import build_config
from model.schema.url import DATABASE_URL_ENV_VAR

#: The revision under test, and the one immediately before it. `0103` is the
#: parent `0300` declares; naming it here rather than computing it keeps the
#: test honest about which boundary it is standing on — and if `0300` is ever
#: re-parented onto another Wave-4 head, this is the one line that moves.
GUARDED_REVISION = "0300"
REVISION_BEFORE_THE_GUARD = "0103"

#: Where a scratch database is created, and the name space it is created in.
#: `postgres` is the maintenance database every PostgreSQL install carries;
#: neither `CREATE DATABASE` nor `DROP DATABASE` runs inside a transaction
#: block, so the connection is opened in AUTOCOMMIT.
MAINTENANCE_DATABASE = "postgres"
SCRATCH_DATABASE_PREFIX = "e007_migration_guard_"
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9_]+$")

#: Module-level SQL, never assembled from values (Ruff S608). A run row in the
#: shape `0008` declares — **not** E007's, because at `0103` the fourteen
#: columns this test is about do not exist yet. Fixture-shaped values
#: throughout: what matters is that the row is *there*, not what it says.
FIXTURE_RUN_INSERT = text(
    """
    INSERT INTO forecast_run (
        run_id, code_commit, code_worktree_dirty, input_data_hash, seed_entropy,
        chain_count, draw_count, tuning_count, library_versions, artifact_hash,
        draw_serialization, artifact_schema_version, model_version, as_of_date,
        horizon_days, wall_clock_seconds, roster_hash
    )
    VALUES (
        :run_id, :code_commit, false, :input_data_hash, '17', 4, 5, 1,
        CAST(:library_versions AS jsonb), :artifact_hash, 'float64-le-c-contiguous',
        1, 'fixture-model', DATE '2026-04-01', 3, 0.0, :roster_hash
    )
    """
)
RUN_COUNT_SQL = text("SELECT count(*) FROM forecast_run")
COLUMN_PRESENT_SQL = text(
    """
    SELECT count(*) FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'forecast_run'
      AND column_name = :column_name
    """
)
FUNCTION_PRESENT_SQL = text(
    "SELECT count(*) FROM pg_proc WHERE proname = 'fn_vendor_shrinkage_wellformed'"
)

#: One of the fourteen, probed to show a refused migration added none of them.
#: `open_line_count` rather than the first alphabetically: it is the column
#: FR-021 is made structural by, so its absence is the most consequential.
A_GUARDED_COLUMN = "open_line_count"

FIXTURE_RUN_PARAMETERS = {
    "run_id": uuid.uuid4(),
    "code_commit": "0" * 40,
    "input_data_hash": "sha256:" + "11" * 32,
    "library_versions": (
        '{"pymc": "6.2.0", "arviz": "1.2.0", "numpy": "2.4.6", '
        '"pandas": "3.0.5", "pytensor": "2.36.1", "blas": "openblas"}'
    ),
    "artifact_hash": b"\x00" * 32,
    "roster_hash": "sha256:" + "22" * 32,
}


#: The name `0300` declares its refusal under. Matched by **type name** rather
#: than by identity, and that is forced rather than chosen: Alembic loads each
#: version file through `util.load_python_file`, which builds a *fresh* module
#: object per `ScriptDirectory` — so the class an ordinary import yields is not
#: the class the migration raises, and `isinstance` would be false against a
#: refusal that did happen. Matching the name over a `RuntimeError` base keeps
#: this a claim about the type: a not-null violation from the server arrives as
#: a `DBAPIError`, which is not a `RuntimeError` at all, so the two categories
#: the requirement separates stay distinguishable.
GUARD_ERROR_NAME = "ForecastRunNotEmptyError"


@pytest.fixture
def scratch_database(database_url: URL, monkeypatch: pytest.MonkeyPatch) -> Iterator[URL]:
    """An empty database of its own, created for one test and dropped after it.

    `DATABASE_URL` is repointed at it for the duration, because that variable is
    the only channel the Alembic environment reads — steering the run any other
    way would test a code path the deployed runner does not use. `monkeypatch`
    restores the original even on failure, so the shared database this tier
    otherwise uses is never touched.
    """
    name = f"{SCRATCH_DATABASE_PREFIX}{uuid.uuid4().hex}"
    if not SAFE_IDENTIFIER_PATTERN.fullmatch(name):
        raise AssertionError(f"refusing to build DDL around the identifier {name!r}")
    maintenance = create_engine(
        database_url.set(database=MAINTENANCE_DATABASE),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        with maintenance.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        scratch_url = database_url.set(database=name)
        try:
            monkeypatch.setenv(
                DATABASE_URL_ENV_VAR, scratch_url.render_as_string(hide_password=False)
            )
            yield scratch_url
        finally:
            with maintenance.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        maintenance.dispose()


def _scratch_engine(url: URL) -> Engine:
    """A short-lived engine on the scratch database, never pooled.

    `NullPool` because the caller is usually about to drop this database, and a
    pooled connection left open would make `DROP DATABASE` fail for a reason
    that has nothing to do with the test.
    """
    return create_engine(url, poolclass=NullPool)


def test_the_guard_refuses_a_populated_forecast_run(scratch_database: URL) -> None:
    """FR-036 / SC-037: the migration's own named error, not a not-null violation.

    The chain is applied up to `0103` — the revision immediately before the
    guard — so `forecast_run` exists in the shape `0008` declared and the
    fourteen columns do not. One run row is committed into it, and `0300` is
    then asked to apply.
    """
    command.upgrade(build_config(), REVISION_BEFORE_THE_GUARD)
    engine = _scratch_engine(scratch_database)
    try:
        with engine.begin() as connection:
            connection.execute(FIXTURE_RUN_INSERT, FIXTURE_RUN_PARAMETERS)
        with engine.connect() as connection:
            assert connection.execute(RUN_COUNT_SQL).scalar_one() == 1, (
                "the fixture run was not committed, so the guard would find an empty table "
                "and this test would assert nothing"
            )

        with pytest.raises(RuntimeError) as refused:
            command.upgrade(build_config(), GUARDED_REVISION)

        assert type(refused.value).__name__ == GUARD_ERROR_NAME, (
            f"the migration refused with a {type(refused.value).__name__}; FR-036 requires "
            f"its own named error rather than the server's not-null violation, so an "
            f"operator reads the condition instead of the symptom"
        )
        assert "0300" in str(refused.value)
        assert "1 row(s)" in str(refused.value), (
            f"the error does not state how many rows stand in the way, which is the "
            f"difference between an operator deleting one stray fixture row and one "
            f"discovering the database is in production use: {refused.value}"
        )
    finally:
        engine.dispose()


def test_a_refused_guard_leaves_the_schema_exactly_as_it_found_it(
    scratch_database: URL,
) -> None:
    """The guard runs **before any DDL**, so a refusal has nothing to undo.

    Stated separately because it fails differently: a migration that raised the
    right error after issuing half its statements would satisfy the test above
    and leave a database carrying an immutable helper and some of fourteen
    columns, with `alembic_version` still reading `0103`. Neither the function
    nor the column may exist afterwards.
    """
    command.upgrade(build_config(), REVISION_BEFORE_THE_GUARD)
    engine = _scratch_engine(scratch_database)
    try:
        with engine.begin() as connection:
            connection.execute(FIXTURE_RUN_INSERT, FIXTURE_RUN_PARAMETERS)

        with pytest.raises(RuntimeError) as refused:
            command.upgrade(build_config(), GUARDED_REVISION)

        assert type(refused.value).__name__ == GUARD_ERROR_NAME
        with engine.connect() as connection:
            column = connection.execute(
                COLUMN_PRESENT_SQL, {"column_name": A_GUARDED_COLUMN}
            ).scalar_one()
            helper = connection.execute(FUNCTION_PRESENT_SQL).scalar_one()
            stamped = set(
                connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
            )

        assert column == 0, (
            f"`forecast_run.{A_GUARDED_COLUMN}` exists after a refused migration, so the "
            f"guard ran after some of the DDL rather than before all of it"
        )
        assert helper == 0, "`fn_vendor_shrinkage_wellformed` was created by a refused run"
        assert stamped == {REVISION_BEFORE_THE_GUARD}
    finally:
        engine.dispose()


def test_the_guard_applies_cleanly_against_an_empty_forecast_run(
    scratch_database: URL,
) -> None:
    """SC-037's other half, without which "always refuse" would pass this file.

    Applied as part of the whole chain to head, which is how the migration is
    ever actually run: `0300` against an empty table adds its helper and its
    fourteen columns, and `0301`–`0303` build on them. Asserting over `0300`
    alone would leave the columns unexercised by anything downstream.
    """
    command.upgrade(build_config(), "head")
    heads = set(ScriptDirectory.from_config(build_config()).get_heads())
    engine = _scratch_engine(scratch_database)
    try:
        with engine.connect() as connection:
            column = connection.execute(
                COLUMN_PRESENT_SQL, {"column_name": A_GUARDED_COLUMN}
            ).scalar_one()
            helper = connection.execute(FUNCTION_PRESENT_SQL).scalar_one()
            runs = connection.execute(RUN_COUNT_SQL).scalar_one()
            stamped = set(
                connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
            )
    finally:
        engine.dispose()

    assert runs == 0, "the scratch database is not empty, so the guard was never in play"
    assert column == 1
    assert helper == 1
    assert stamped == heads
