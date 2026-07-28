"""Fixtures for the E007 forecast tier: a live database at head, and tmp roots.

Modelled on `tests/schema/conftest.py`, whose transaction-rollback isolation is
reused here rather than reinvented, and on `tests/procurement/conftest.py` for
the CI guard. Where this tier differs from either is stated below; everything
not stated is the same by intent.

**Where the database comes from.** `DATABASE_URL`, resolved by
`model.schema.url.get_database_url` — the same function the Alembic environment,
the schema tier and the procurement tier use, so migrations and tests cannot
disagree about which database they mean. `tasks.md` describes this tier as
running against PostgreSQL on `${PRC_DB_PORT:-5434}`; that is where
`docker-compose.yml` *publishes* the service, not a second configuration
channel, and it is deliberately not read here. A port literal in this file would
be wrong on CI, where the service container answers on 5432, and wrong in any
checkout that set `PRC_DB_PORT` to dodge a collision with a sibling.

**Isolation is by rolled-back outer transaction, as in the schema tier.** Every
test runs inside a transaction that is discarded in teardown, with the `Session`
joined to it by savepoint, so nothing a test writes is ever committed and no
`TRUNCATE` or cleanup step is needed. This tier can use that form — unlike the
procurement tier, which could not, because the loader under test commits in
order to reach the deferred closing-event foreign key. **E007 declares no
deferrable constraint** (`data-model.md` § Delivered Audits This Epic Must Not
Break), so nothing in the five tables this tier writes to is checked only at
`COMMIT`, and the cheaper, order-independent isolation is sound here.

That is a property of the *schema*, not a promise about the code under test, and
it is worth naming the one case it does not cover: `write.py`'s transaction 2 —
the `is_active` flip — is a separate transaction by design. A test of the
pointer's behaviour across two commits belongs on a scratch database of its own,
in the shape `tests/schema/test_migration_chain.py::scratch_database` already
establishes, not on `db_session`.

**The head assertion, and why it is a fixture rather than a test.** The fit job
refuses to run when the schema head is not `0303`, because at `0302` every write
it performs would succeed except the one recording *why* the run was allowed to
write at all (`data-model.md` § Migration Sequence). A test tier reading a
database one revision short would report a missing table as a failing assertion
about the code. `schema_at_head` turns that into one legible error naming the
observed revision and the command that fixes it, and every database fixture here
depends on it, so no test in this tier can run against a stale schema.

**Skipping, and the gate that stops a skip from passing for a pass.** With
`DATABASE_URL` unset the database tests here skip, so the property, unit and
build-gating tiers still run on a machine with no database. E003's QC measured
what that permissiveness costs unguarded — a run reporting *81 passed, 344
skipped, exit 0* — so the skip is opt-out: `REQUIRE_DB`, or `CI` being set, turns
a missing variable into one hard error before any test runs. The gate is
re-derived here rather than imported, for the reason the procurement tier
records: two files both named `conftest` cannot be imported by name without
putting an ambiguous module on `sys.path`, and neither is the other's parent
directory. The variable names are deliberately identical, so one `REQUIRE_DB=1`
configures every tier rather than each needing its own switch.

**No `__init__.py` in this directory, deliberately.** Neither `tests/schema` nor
`tests/procurement` carries one; pytest's default import mode puts each test
directory on `sys.path` and distinguishes modules by basename, so a package
marker here would change how three sibling suites are imported to solve a
problem none of them has. Test module basenames in this tier must therefore stay
unique across `src/model/tests`, which is the cost of that arrangement and is
stated so the next author knows it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.orm import Session

from model.schema.cli import build_config
from model.schema.url import (
    DATABASE_URL_ENV_VAR,
    DatabaseUrlNotConfiguredError,
    get_database_url,
)

#: Demand a database: an unset `DATABASE_URL` then aborts the run instead of
#: skipping this tier. Same spelling as the schema and procurement tiers', on
#: purpose.
REQUIRE_DATABASE_ENV_VAR = "REQUIRE_DB"

#: Set by GitHub Actions and by every other CI provider, so a *new* workflow
#: step that runs this suite is strict without anyone having opted in.
#: `REQUIRE_DB=0` overrides it for a job that legitimately has no database.
CI_ENV_VAR = "CI"

#: Spellings of "no" accepted in either variable. An unset or blank value is
#: *absent* rather than false and defers to the next channel.
FALSE_VALUES = frozenset({"0", "false", "no", "off"})

#: Alembic's bookkeeping table, read to learn which revision the database is at.
#: Module-level SQL, never assembled from values (Ruff S608).
STAMPED_REVISIONS_SQL = "SELECT version_num FROM alembic_version"

#: The command that repairs a stale schema. Named in the failure message so the
#: fix is in the error rather than in somebody's memory.
MIGRATE_COMMAND = "uv run --directory src/model migrate"

#: Where a test's emitted reports go. A single directory name under `tmp_path`
#: rather than the real `data/` tree: FR-037 and FR-040 make the fit job write
#: files on both the success and the refusal path, and a tier that let those
#: land in the checkout would leave a passing run indistinguishable from one
#: that had quietly overwritten a committed artifact.
REPORT_ROOT_NAME = "forecast-reports"


def database_is_required() -> bool:
    """Whether an unset `DATABASE_URL` should abort the run rather than skip it.

    `REQUIRE_DB` wins when it says anything at all, including when it says no;
    otherwise `CI` decides. Both are read case-insensitively and both treat
    blank as unset, because an exported-but-empty variable is far more often a
    broken shell than a considered instruction.
    """
    explicit = os.environ.get(REQUIRE_DATABASE_ENV_VAR, "").strip().lower()
    if explicit:
        return explicit not in FALSE_VALUES
    return os.environ.get(CI_ENV_VAR, "").strip().lower() not in ({""} | FALSE_VALUES)


def pytest_configure(config: pytest.Config) -> None:
    """Refuse to start when a database is required and none is configured.

    A historic hook, so pluggy replays it when collection reaches this
    directory: the check fires only if a forecast test was actually selected,
    and it reports the problem once, before any test runs, rather than once per
    failing fixture. `UsageError` is the accurate category — nothing is wrong
    with the code, the environment was asked for a database and did not name
    one.
    """
    del config  # required by the hook signature; nothing here is configurable
    if not database_is_required():
        return
    try:
        get_database_url()
    except DatabaseUrlNotConfiguredError as exc:
        raise pytest.UsageError(
            f"{DATABASE_URL_ENV_VAR} is unset but {REQUIRE_DATABASE_ENV_VAR} or "
            f"{CI_ENV_VAR} says a database is required, so the E007 forecast tier would "
            f"have skipped and the run would have reported success having asserted "
            f"nothing about the artifact stores, the write order, the refusal guarantee "
            f"or the reproduction harness. Refusing to start. Export "
            f"{DATABASE_URL_ENV_VAR}, or set {REQUIRE_DATABASE_ENV_VAR}=0 to allow the "
            f"skip deliberately. Underlying cause: {exc}"
        ) from exc


# --------------------------------------------------------------------------
# The live database
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def database_url() -> URL:
    """The connection target, or a skip for the whole database tier.

    Skipping covers only "this machine has not been told where the database
    is", and only when `database_is_required()` is false — `pytest_configure`
    has already aborted otherwise, so this skip is never the thing that hides a
    missing database in CI. A `DATABASE_URL` that is set but unreachable, or
    that points at a database short of E007's revisions, is a broken environment
    and fails loudly in every mode.
    """
    try:
        return get_database_url()
    except DatabaseUrlNotConfiguredError as exc:
        pytest.skip(f"the E007 forecast tier needs a database: {exc}")


@pytest.fixture(scope="session")
def engine(database_url: URL) -> Iterator[Engine]:
    """One engine for the whole run, disposed at the end.

    Session-scoped because connecting is the slow part and every test in this
    tier wants the same database. `pool_pre_ping` because the Compose service
    can be restarted between runs, and a stale pooled socket should cost one
    silent reconnect rather than one confusing test failure.
    """
    created = create_engine(database_url, pool_pre_ping=True)
    try:
        yield created
    finally:
        created.dispose()


@pytest.fixture(scope="session")
def chain_head() -> str:
    """The single head of the revision chain on disk, as the runner resolves it.

    Read from `ScriptDirectory` through `build_config()` — the same function the
    `migrate` console entry point uses — rather than written here as the literal
    `"0303"`. The literal would be a fourth place the head is recorded and the
    first to go stale: E007 re-parents `0300` if a sibling Wave-4 epic lands
    first, and E008's own revisions will move the head again. What this tier
    actually needs is that the database agrees with the chain *this checkout*
    would apply, which is what comparing against the resolved head asserts.

    Single-headedness is not re-asserted here; `test_migration_chain.py` and
    `tests/checks/test_migration_ranges.py` own that claim (DV-033), and this
    fixture failing on a multi-head chain would report their defect as this
    tier's.
    """
    heads = ScriptDirectory.from_config(build_config()).get_heads()

    assert len(heads) == 1, (
        f"the revision chain resolves to {len(heads)} heads ({sorted(heads)}), so there is "
        f"no single revision this tier's database could be expected to sit at. That is a "
        f"chain defect, not a forecast one — see test_migration_chain.py."
    )
    return heads[0]


@pytest.fixture(scope="session")
def schema_at_head(engine: Engine, chain_head: str) -> str:
    """Assert the database has E007's revisions applied, before any test reads it.

    The chain is linear and single-headed, so a database stamped at the head has
    necessarily applied every ancestor of it — `0300` through `0303` included.
    That is why this compares against the head rather than probing for the three
    tables by name: a table probe would pass on a schema somebody built by hand
    with the right relations and the wrong constraints, which is precisely the
    route `data-model.md` § Migration Sequence forbids (DV-034).

    Session-scoped and depended on by `db_session`, so the check runs once and
    covers every database test in the tier.
    """
    with engine.connect() as connection:
        stamped = set(connection.execute(text(STAMPED_REVISIONS_SQL)).scalars())

    assert stamped == {chain_head}, (
        f"the database is stamped {sorted(stamped) or '(nothing)'} but the chain on disk "
        f"resolves to head {chain_head!r}. E007's artifact stores are created by "
        f"`0301`–`0303`, and the fit job itself refuses to run below head for the same "
        f"reason this tier does: at `0302` every write succeeds except the one recording "
        f"why the run was allowed to write at all. Bring the database up with "
        f"`{MIGRATE_COMMAND}` rather than creating the missing objects by hand."
    )
    return chain_head


@pytest.fixture
def db_session(engine: Engine, schema_at_head: str) -> Iterator[Session]:
    """A transactional `Session` whose every write is discarded in teardown.

    The shape is the schema tier's, unchanged: open a connection, begin an outer
    transaction on it, and bind a `Session` to that connection with
    `join_transaction_mode="create_savepoint"`. The session then works inside a
    `SAVEPOINT`, so even an explicit `session.commit()` only releases and
    re-opens that savepoint — the outer transaction is never committed and is
    rolled back here, unconditionally. That is what makes the tier
    order-independent and free of cleanup code, and it is what lets a test write
    a whole run's artifact set into a database that also holds E005's loaded
    lines without disturbing them.

    Yields a SQLAlchemy ORM `Session`, but there are no mapped classes in this
    package: the schema is authored as explicit DDL, so tests drive it with
    `session.execute(text(...))`. The `Session` is used rather than a bare Core
    `Connection` for exactly one property — on a `Connection`, a stray
    `commit()` in a test would commit for real and leak rows into the next test.
    """
    del schema_at_head  # requested for its assertion, not for its value
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


# --------------------------------------------------------------------------
# Emitted-artifact roots
# --------------------------------------------------------------------------


@pytest.fixture
def report_root(tmp_path: Path) -> Path:
    """A throwaway root for the files a fit or a refusal emits.

    Returned as a *root* rather than as a file path because
    `model.forecast.paths` resolves every emitted filename from one, in the same
    shape `model.procurement.paths` already establishes: a test drives the real
    write path — the real filenames, the real directory layout — without
    touching the committed artifacts. Building the paths inside a test instead
    would leave the test agreeing with its own idea of the layout rather than
    with the one the job uses.

    The directory exists on arrival so that a test asserting "exactly these
    files were emitted" is measuring what the job wrote rather than which
    directories it happened to create — and so that the refusal path, which
    must emit a report (FR-037), cannot be excused by a missing parent.
    """
    root = tmp_path / REPORT_ROOT_NAME
    root.mkdir()
    return root
