"""Fixtures for the E005 procurement tier: a live database, and tmp artifact roots.

Modelled on `tests/schema/conftest.py`, and it differs from it in exactly one
place that matters. That tier wraps every test in an outer transaction it rolls
back, so nothing it writes is ever committed. **This tier cannot do that**: the
loader under test opens its own `REPEATABLE READ` transaction and *commits*,
because `fk_purchase_order_line__closing_event` is `DEFERRABLE INITIALLY
DEFERRED` and only validates at `COMMIT` (FR-029, SC-011). A harness that never
reached a commit would let a closed line naming a nonexistent event pass, which
is the one thing the closure test exists to catch. So isolation here is by
`TRUNCATE` on both sides of each test instead, which is safe because E005 is the
only writer of these two tables in this project.

**Where the database comes from.** `DATABASE_URL`, resolved by
`model.schema.url.get_database_url` — the same function the Alembic environment
and the schema tier use, so migrations and tests cannot disagree about which
database they mean. `tasks.md` describes this tier as running against
PostgreSQL on `${PRC_DB_PORT:-5434}`; that is where `docker-compose.yml`
*publishes* the service, not a second configuration channel, and it is
deliberately not read here. A port literal in this file would be wrong on CI,
where the service container answers on 5432, and wrong in any checkout that set
`PRC_DB_PORT` to dodge a collision with a sibling — which is exactly why E001
froze `DATABASE_URL` as the only channel and why `url.py` refuses to guess.

**Skipping, and the gate that stops a skip from passing for a pass.** With no
`DATABASE_URL` the integration tests here skip, so the property, unit and
build-gating tiers still run on a machine with no database. E003's QC measured
what that permissiveness costs when it is unguarded — a run reporting *81
passed, 344 skipped, exit 0* — so the skip is opt-out: `REQUIRE_DB`, or `CI`
being set, turns a missing variable into one hard error before any test runs.

The gate is re-derived here rather than imported from `tests/schema/conftest.py`
because two files both named `conftest` cannot be imported by name without
putting an ambiguous module on `sys.path`, and neither is the other's parent
directory. The environment-variable names are deliberately identical, so one
`REQUIRE_DB=1` configures both tiers rather than each needing its own switch.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import URL, Engine, create_engine

from model.procurement.paths import ground_truth_dir, procurement_dir
from model.schema.url import (
    DATABASE_URL_ENV_VAR,
    DatabaseUrlNotConfiguredError,
    get_database_url,
)

#: Demand a database: an unset `DATABASE_URL` then aborts the run instead of
#: skipping this tier. Same spelling as the schema tier's, on purpose.
REQUIRE_DATABASE_ENV_VAR = "REQUIRE_DB"

#: Set by GitHub Actions and by every other CI provider, so a *new* workflow
#: step that runs this suite is strict without anyone having opted in.
#: `REQUIRE_DB=0` overrides it for a job that legitimately has no database.
CI_ENV_VAR = "CI"

#: Spellings of "no" accepted in either variable. An unset or blank value is
#: *absent* rather than false and defers to the next channel.
FALSE_VALUES = frozenset({"0", "false", "no", "off"})

#: Emptying the two tables E005 writes: events first, then lines.
#:
#: `DELETE` rather than `TRUNCATE`, and that is not a style choice. Two tables
#: owned by *other* epics already reference `purchase_order_line` —
#: `line_posterior` (`fk_line_posterior__line`) and `resolved_entity_member`
#: (`fk_rem__po_line`) — so `TRUNCATE` refuses outright and `TRUNCATE … CASCADE`
#: would silently empty both of them. A cleanup fixture that destroys another
#: epic's rows to tidy up after this one is a worse outcome than a failing test.
#: `DELETE` refuses loudly if anything downstream still references a line, which
#: is the report a developer needs.
#:
#: The order is forced. Deleting events first momentarily dangles
#: `purchase_order_line.closing_event_id`, which is legal because that
#: constraint is the schema's one `DEFERRABLE INITIALLY DEFERRED` — it is
#: validated at `COMMIT`, by which point both tables are empty.
#:
#: Module constants rather than f-strings built at a call site: Ruff S608 exists
#: because SQL assembled from values is how injection happens, and there is no
#: value here to assemble.
DELETE_EVENTS = "DELETE FROM lifecycle_event"
DELETE_LINES = "DELETE FROM purchase_order_line"
EMPTY_PROCUREMENT_TABLES = (DELETE_EVENTS, DELETE_LINES)

#: Row counts, for the "the refusal left the database unchanged" assertions
#: (DV-027, NC-9) that several tests in this tier make.
COUNT_LINES = "SELECT count(*) FROM purchase_order_line"
COUNT_EVENTS = "SELECT count(*) FROM lifecycle_event"


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
    directory: the check fires only if a procurement test was actually
    selected, and it reports the problem once, before any test runs, rather than
    once per failing fixture. `UsageError` is the accurate category — nothing is
    wrong with the code, the environment was asked for a database and did not
    name one.
    """
    del config  # required by the hook signature; nothing here is configurable
    if not database_is_required():
        return
    try:
        get_database_url()
    except DatabaseUrlNotConfiguredError as exc:
        raise pytest.UsageError(
            f"{DATABASE_URL_ENV_VAR} is unset but {REQUIRE_DATABASE_ENV_VAR} or "
            f"{CI_ENV_VAR} says a database is required, so the E005 loader tier would "
            f"have skipped and the run would have reported success having asserted "
            f"nothing about loading, idempotency, refusal or deferred closure. Refusing "
            f"to start. Export {DATABASE_URL_ENV_VAR}, or set "
            f"{REQUIRE_DATABASE_ENV_VAR}=0 to allow the skip deliberately. "
            f"Underlying cause: {exc}"
        ) from exc


# --------------------------------------------------------------------------
# The live database
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def database_url() -> URL:
    """The connection target, or a skip for the whole integration tier.

    Skipping covers only "this machine has not been told where the database
    is", and only when `database_is_required()` is false — `pytest_configure`
    has already aborted otherwise. A `DATABASE_URL` that is set but unreachable
    is a broken environment and fails loudly in every mode.
    """
    try:
        return get_database_url()
    except DatabaseUrlNotConfiguredError as exc:
        pytest.skip(f"the E005 loader tier needs a database: {exc}")


@pytest.fixture(scope="session")
def libpq_conninfo(database_url: URL) -> str:
    """`database_url` as a libpq connection string psycopg 3 will accept.

    `get_database_url` returns a SQLAlchemy `URL` whose drivername is
    `postgresql+psycopg`, which selects the DBAPI. libpq does not know what that
    means, so the driver suffix is dropped for a direct connection. The password
    has to be rendered here — that is what connecting requires — which is why
    this returns a string a caller passes straight to `connect` rather than
    something anyone would be tempted to log.
    """
    return database_url.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def engine(database_url: URL) -> Iterator[Engine]:
    """One SQLAlchemy engine for the whole run, disposed at the end.

    Session-scoped because connecting is the slow part. `pool_pre_ping` because
    the Compose service can be restarted between runs, and a stale pooled socket
    should cost one silent reconnect rather than one confusing failure.
    """
    created = create_engine(database_url, pool_pre_ping=True)
    try:
        yield created
    finally:
        created.dispose()


@pytest.fixture
def pg_connection(libpq_conninfo: str) -> Iterator[psycopg.Connection]:
    """A raw psycopg 3 connection, which is what the loader itself uses.

    Not autocommit: the loader's whole contract is about what happens inside one
    transaction and at its commit, so a fixture that committed each statement
    would dissolve the thing under test.
    """
    with psycopg.connect(libpq_conninfo) as connection:
        yield connection


def _empty(connection: psycopg.Connection) -> None:
    """Delete every E005 row in one transaction, then commit.

    One transaction for both statements, because the deferred closing-event
    foreign key is only satisfied once both tables are empty.
    """
    with connection.cursor() as cursor:
        for statement in EMPTY_PROCUREMENT_TABLES:
            cursor.execute(statement)
    connection.commit()


@pytest.fixture
def empty_procurement_tables(pg_connection: psycopg.Connection) -> Iterator[psycopg.Connection]:
    """Both tables empty before the test and empty again after it.

    Emptied on both sides rather than only one. Doing it on the way *in* makes
    the tier order-independent and survives a previous run that was interrupted
    before its teardown; doing it on the way *out* leaves the developer's
    database as it was found. E005 is the only *writer* of these two tables, so
    nothing else is standing in the blast radius — but two other epics read them
    through foreign keys, which is why this deletes rather than truncates.

    The teardown rolls back first. A test that asserted a refusal has left an
    aborted or open transaction behind, and issuing the cleanup on it would fail
    with "current transaction is aborted" rather than cleaning anything.
    """
    _empty(pg_connection)
    try:
        yield pg_connection
    finally:
        pg_connection.rollback()
        _empty(pg_connection)


@pytest.fixture
def row_counts(pg_connection: psycopg.Connection):
    """`() -> (lines, events)`, on a connection of the caller's choosing.

    Returned as a callable rather than as a value because every use of it is a
    *before and after* comparison: DV-027 requires a refusal to leave the
    database unchanged, and "unchanged" is two observations of the same query,
    not one.
    """

    def counts(connection: psycopg.Connection | None = None) -> tuple[int, int]:
        target = connection if connection is not None else pg_connection
        with target.cursor() as cursor:
            cursor.execute(COUNT_LINES)
            lines = cursor.fetchone()
            cursor.execute(COUNT_EVENTS)
            events = cursor.fetchone()
        if lines is None or events is None:  # pragma: no cover - count() always returns a row
            raise AssertionError("a COUNT(*) returned no row")
        return (lines[0], events[0])

    return counts


# --------------------------------------------------------------------------
# Artifact roots
# --------------------------------------------------------------------------


@pytest.fixture
def artifact_root(tmp_path: Path) -> Path:
    """A throwaway repository root with both emission trees already created.

    Every path resolver in `model.procurement.paths` takes an optional root for
    this: a test drives the *real* write path — the real filenames, the real two
    trees, the real separation between them — without touching the committed
    artifacts. Building the paths in the test instead would leave the test
    agreeing with its own idea of the layout rather than with the one the
    generator uses.

    Both directories exist on arrival so that a test asserting "exactly these
    four files were emitted" (DV-020) is measuring what the generator wrote
    rather than which directories it happened to create.
    """
    procurement_dir(tmp_path).mkdir(parents=True)
    ground_truth_dir(tmp_path).mkdir(parents=True)
    return tmp_path
