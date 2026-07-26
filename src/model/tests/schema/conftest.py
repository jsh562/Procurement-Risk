"""Fixtures for the schema integration tier (TR-042).

These tests run against a *real* PostgreSQL 16 with pgvector -- the same
digest-pinned image the application runs -- because everything under test here
is server-side behaviour: named `CHECK` constraints, composite foreign keys,
generated columns, partial unique indexes, and one deferrable constraint. None
of that has a faithful mock.

**Where the database comes from (AD-001).** The already-running Compose `db`
service, addressed through `DATABASE_URL`, and never a hardcoded host or port.
That port is 5434 locally and 5432 for the CI service container, so any literal
here would be wrong in one of the two. The variable is resolved by
`model.schema.url.get_database_url`, the same function the Alembic environment
uses, so the tests and the migrations cannot disagree about which database or
which driver they mean.

**Skipping, and the gate that stops a skip from passing for a pass.** With
`DATABASE_URL` unset this tier skips, so the static checks in the same suite --
single-head, the source scans, the AST checks -- still run on a machine with no
database. That permissiveness is deliberate and it is also dangerous: measured,
`env -u DATABASE_URL pytest tests -q` reported *81 passed, 344 skipped, exit 0*.
Four fifths of the suite vanished and the run still looked green. So the skip is
opt-out: see `database_is_required` and `pytest_configure` below, which turn the
missing variable into a single hard error wherever the environment says a
database was supposed to be there.

**Isolation.** Every test runs inside an outer transaction that is rolled back
in teardown, with the `Session` joined to it by savepoint. Nothing a test writes
is ever committed, so tests cannot see each other's rows and no cleanup or
`TRUNCATE` step is needed. `session.commit()` inside a test is safe -- it
releases a savepoint and opens the next one; the outer transaction is still
discarded.

**The trap that isolation creates.** A `DEFERRABLE INITIALLY DEFERRED`
constraint is not checked until `COMMIT`, and this harness never reaches one.
A test that inserts a violating row and asserts nothing happened would therefore
pass while proving nothing. The schema has exactly one such constraint,
`fk_purchase_order_line__closing_event` (data-model.md, TR-021). Use
`force_constraints_immediate` to make it fire at a point the test chooses --
see that function's docstring for why that mechanism was picked over a
committing session, and for the two-step shape a deferral test must have to be
worth anything.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

import psycopg
import pytest
from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from model.schema.url import (
    DATABASE_URL_ENV_VAR,
    DatabaseUrlNotConfiguredError,
    get_database_url,
)

#: Forces PostgreSQL to check every constraint deferred so far, immediately.
#: A module constant, not an f-string built at the call site: Ruff S608 exists
#: because SQL assembled from values is how injection happens, and there is no
#: value here to assemble.
SET_CONSTRAINTS_IMMEDIATE = "SET CONSTRAINTS ALL IMMEDIATE"

#: Set this to demand a database: an unset `DATABASE_URL` then aborts the run
#: instead of skipping this tier. The channel is an environment variable rather
#: than a `--require-db` command-line flag for two reasons. `pytest_addoption`
#: may only be implemented in an *initial* conftest, so a flag would mean adding
#: a `tests/conftest.py` whose only content is the registration of one option;
#: and CI reaches this suite through `coverage run -m pytest`, where the
#: DATABASE_URL it must agree with is already supplied as an `env:` entry -- so
#: the two settings that have to be changed together end up in the same block,
#: which a flag on the `run:` line would separate.
REQUIRE_DATABASE_ENV_VAR = "REQUIRE_DB"

#: Set by GitHub Actions and by every other CI provider. Consulted so that the
#: guarantee is structural: a *new* workflow step that runs this suite is strict
#: without anyone having remembered to opt in, which is the failure mode
#: `REQUIRE_DB` alone would still have. `REQUIRE_DB=0` overrides it for the case
#: of a CI job that legitimately has no database.
CI_ENV_VAR = "CI"

#: Spellings of "no" accepted in either variable. An unset or blank value is
#: *absent* rather than false, and defers to the next channel.
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def database_is_required() -> bool:
    """Whether an unset `DATABASE_URL` should abort the run rather than skip it.

    `REQUIRE_DB` wins when it says anything at all, including when it says no.
    Otherwise `CI` decides. Both are read case-insensitively and both treat
    blank as unset, because an exported-but-empty variable is far more often a
    broken shell than a considered instruction.
    """
    explicit = os.environ.get(REQUIRE_DATABASE_ENV_VAR, "").strip().lower()
    if explicit:
        return explicit not in FALSE_VALUES
    return os.environ.get(CI_ENV_VAR, "").strip().lower() not in ({""} | FALSE_VALUES)


def pytest_configure(config: pytest.Config) -> None:
    """Refuse to start when a database is required and none is configured.

    `pytest_configure` is a *historic* hook, so pluggy replays it to this
    conftest at the moment collection reaches this directory -- which is what
    makes the check possible here at all, and what keeps it beside the fixture it
    guards. It therefore fires only when a schema test was actually selected:
    `REQUIRE_DB=1 pytest tests/test_roster_reader.py` runs normally, because
    nothing was going to be skipped.

    Three properties come out of doing it here rather than in the `database_url`
    fixture:

    * It is **one** message, not one per test. Failing the session-scoped
      fixture instead reports the same problem 344 times and buries the sentence
      that says how to fix it.
    * It happens before any test runs, so it cannot be mistaken for a test
      failure or attributed to whichever test was collected first.
    * It aborts, rather than continuing with a suite whose database tier is
      known to be inert.

    `UsageError` is the accurate category and is chosen over `pytest.exit` for
    that reason: nothing is wrong with the code or the schema, the environment
    was asked for a database and did not name one. Raised inside collection it
    surfaces as `ERROR tests/schema` and interrupts the session with exit code 2
    -- non-zero, with the whole message in the summary, which is the entire
    point.
    """
    del config  # required by the hook signature; nothing here is configurable
    if not database_is_required():
        return
    try:
        get_database_url()
    except DatabaseUrlNotConfiguredError as exc:
        raise pytest.UsageError(
            f"{DATABASE_URL_ENV_VAR} is unset but {REQUIRE_DATABASE_ENV_VAR} or "
            f"{CI_ENV_VAR} says a database is required, so the schema integration "
            f"tier would have skipped and the run would have reported success "
            f"having asserted nothing about the schema. Refusing to start. "
            f"Export {DATABASE_URL_ENV_VAR}, or set "
            f"{REQUIRE_DATABASE_ENV_VAR}=0 to allow the skip deliberately. "
            f"Underlying cause: {exc}"
        ) from exc


@pytest.fixture(scope="session")
def database_url() -> URL:
    """The connection target, or a skip for the whole tier if none is configured.

    Skipping rather than failing is deliberate and narrow: it covers only the
    "this machine has not been told where the database is" case, and only when
    `database_is_required()` is false -- `pytest_configure` has already aborted
    the run otherwise, so this skip is never the thing that hides a missing
    database in CI. A `DATABASE_URL` that is set but unreachable, or points at a
    database missing the `vector` extension, is a broken environment and must
    fail loudly in every mode: silently skipping those would let the integration
    tier report green having asserted nothing.
    """
    try:
        return get_database_url()
    except DatabaseUrlNotConfiguredError as exc:
        pytest.skip(f"schema integration tests need a database: {exc}")


@pytest.fixture(scope="session")
def engine(database_url: URL) -> Iterator[Engine]:
    """One engine for the whole run, disposed at the end.

    Session-scoped because connecting is the slow part and every test in this
    tier wants the same database. `pool_pre_ping` because the Compose service
    can be restarted between runs, and a stale pooled socket should cost one
    silent reconnect rather than one confusing test failure.
    """
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    """A transactional `Session` whose every write is discarded in teardown.

    The shape is: open a connection, begin an outer transaction on it, and bind
    a `Session` to that connection with `join_transaction_mode="create_savepoint"`.
    The session then works inside a `SAVEPOINT`, so even an explicit
    `session.commit()` only releases and re-opens that savepoint -- the outer
    transaction is never committed and is rolled back here, unconditionally.
    That is what makes the tier order-independent and free of cleanup code.

    Yields a SQLAlchemy ORM `Session`, but there are no mapped classes in this
    package: the schema is authored as explicit DDL, so tests drive it with
    `session.execute(text(...))`. The `Session` is used rather than a bare Core
    `Connection` for exactly one property -- on a `Connection`, a stray
    `commit()` in a test would commit for real and leak rows into the next test.

    Deferred constraints are the one thing this fixture cannot observe on its
    own; see `force_constraints_immediate`.
    """
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


@contextmanager
def assert_rejects(
    session: Session,
    error: type[psycopg.Error],
    constraint: str,
) -> Iterator[None]:
    """Assert the wrapped statement is rejected by `constraint`, as `error`.

    Args:
        session: the `db_session` the statement runs on.
        error: the exact psycopg 3 error class expected, e.g.
            `psycopg.errors.CheckViolation` or `psycopg.errors.ForeignKeyViolation`.
            psycopg derives one class per SQLSTATE, so naming the class *is*
            asserting the SQLSTATE -- `CheckViolation` is 23514 and nothing else.
        constraint: the server-side constraint name, matched exactly against the
            error diagnostic.

    Both arguments are required, and that is the point. `pytest.raises(IntegrityError)`
    passes when the *wrong* constraint fires -- when a typo'd fixture row trips a
    `NOT NULL` before ever reaching the `CHECK` under test -- so the test would
    still be green while the constraint it names had never been exercised.
    Nothing is matched against the error *message*: message text is locale- and
    version-dependent, while constraint names are the schema's published
    contract and are asserted elsewhere by the constraint audit.

    SQLAlchemy wraps the driver error, so the psycopg original is on `.orig` and
    the diagnostic is `err.orig.diag.constraint_name`.

    The statement runs inside its own `SAVEPOINT`, which is rolled back on the
    way out. Without that, the failed statement would leave the transaction
    aborted and every later statement in the test would fail with "current
    transaction is aborted" instead of whatever it was actually checking.

    A rejection that never arrives is a failure -- including the case where the
    constraint is `DEFERRABLE INITIALLY DEFERRED`, which will not fire here at
    all. See `force_constraints_immediate`.
    """
    savepoint = session.begin_nested()
    try:
        yield
    except DBAPIError as exc:
        _rollback(savepoint)
        original = exc.orig
        if not isinstance(original, error):
            raise AssertionError(
                f"expected {constraint!r} to reject this with "
                f"{error.__name__} (SQLSTATE {_sqlstate_of(error)}), but the database "
                f"raised {type(original).__name__} "
                f"(SQLSTATE {getattr(original, 'sqlstate', None)}) "
                f"for constraint {_constraint_name_of(original)!r}"
            ) from exc
        actual_constraint = _constraint_name_of(original)
        if actual_constraint != constraint:
            raise AssertionError(
                f"expected {error.__name__} from constraint {constraint!r}, but it came "
                f"from {actual_constraint!r}. The statement was rejected by the wrong "
                f"rule, so the constraint under test was never exercised."
            ) from exc
    else:
        _rollback(savepoint)
        raise AssertionError(
            f"expected {constraint!r} to reject this statement with "
            f"{error.__name__}, but it was accepted. If {constraint!r} is DEFERRABLE "
            f"INITIALLY DEFERRED it is not checked until COMMIT, which this harness "
            f"never reaches -- call force_constraints_immediate(session) inside this "
            f"block to force the check."
        )


def force_constraints_immediate(session: Session) -> None:
    """Check every so-far-deferred constraint now, at this exact point.

    This is the answer to the deferred-constraint problem stated in the module
    docstring, and the mechanism a test author should reach for. `SET CONSTRAINTS
    ALL IMMEDIATE` makes PostgreSQL run the pending checks as part of *this*
    statement, raising the same error class, the same SQLSTATE, and the same
    `constraint_name` diagnostic that `COMMIT` would have raised. So it composes
    with `assert_rejects`:

        with assert_rejects(db_session, ForeignKeyViolation, "fk_..."):
            force_constraints_immediate(db_session)

    Chosen over the alternative -- a second, genuinely committing session -- for
    three reasons. A committing session would put real rows in a shared database,
    which reintroduces the cleanup step and the ordering dependency this tier was
    built to avoid. The failure would surface at `COMMIT`, typically in teardown,
    where it is far harder to attribute to the statement that caused it. And it
    would split the tier across two isolation models, so a test author would have
    to know which fixture a given table belongs to.

    The one thing this does *not* prove is that the constraint is deferrable.
    Forcing an immediate check on a constraint that was never deferred looks
    identical. A deferral test must therefore be two steps, and both matter:

    1. Write the intermediate state that violates the constraint and show it is
       *accepted* -- that is the deferral.
    2. Call this function and show the violation is *then* raised, naming the
       constraint -- that is the enforcement.

    Step 1 without step 2 passes vacuously; step 2 without step 1 would pass just
    as well against an immediate constraint.
    """
    session.execute(text(SET_CONSTRAINTS_IMMEDIATE))


@pytest.fixture(name="assert_rejects")
def assert_rejects_fixture() -> Callable[..., AbstractContextManager[None]]:
    """`assert_rejects` as a fixture, for tests that prefer injection to import.

    Both forms work and are the same object. `from conftest import assert_rejects`
    relies on pytest having put this directory on `sys.path`, which holds today
    but stops holding the moment someone adds an `__init__.py` here; requesting
    the fixture never breaks.
    """
    return assert_rejects


@pytest.fixture(name="force_constraints_immediate")
def force_constraints_immediate_fixture() -> Callable[[Session], None]:
    """`force_constraints_immediate` as a fixture. See `assert_rejects_fixture`."""
    return force_constraints_immediate


def _rollback(savepoint: object) -> None:
    """Roll back `savepoint` if it is still live.

    A `SAVEPOINT` can already be gone by the time we get here -- SQLAlchemy
    deactivates a nested transaction when the driver reports the statement
    failed. Rolling back twice raises, and that exception would replace the
    assertion error we are in the middle of reporting, so the guard is not
    tidiness.
    """
    if getattr(savepoint, "is_active", False):
        savepoint.rollback()  # type: ignore[attr-defined]


def _constraint_name_of(error: BaseException | None) -> str | None:
    """The `constraint_name` diagnostic, or None if this error carries none.

    Defensive on both hops: `diag` is absent on a non-database error, and
    `constraint_name` is None for errors that are not attributable to a named
    constraint at all (a bad cast, say). Either way the caller gets None and
    reports it, rather than raising an `AttributeError` that hides the real
    mismatch.
    """
    diagnostic = getattr(error, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _sqlstate_of(error: type[psycopg.Error]) -> str | None:
    """The SQLSTATE a psycopg error class corresponds to, for failure messages."""
    return getattr(error, "sqlstate", None)
