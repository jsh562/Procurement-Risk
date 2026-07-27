"""Properties of the migration chain itself, as distinct from the schema it builds.

Six obligations are checked here, and each one exists because a specific way of
breaking a deployment is otherwise invisible until the deployment:

* **TR-005 -- one head.** Two people authoring a revision in the same week both
  point `down_revision` at the current tip, and nothing complains until
  `alembic upgrade head` refuses to choose between them. This is the check that
  catches that, and it is a pure file-level check, so it runs without a database.
* **TR-001 -- applies from empty.** A chain that works on *this* database can
  still fail on a fresh one, because "this database" has accumulated objects the
  migrations never created. The only honest test is a genuinely empty database.
* **TR-003 -- idempotent at head.** Re-running must run *nothing*, not merely
  exit zero. Those are different claims and only the first one is TR-003.
* **TR-001/TR-003, OBJ1 VC7 -- a failure leaves no lie behind.** A run that dies
  partway must leave `alembic_version` agreeing with the objects that are
  actually there, and the next run must carry on from that revision with no
  manual repair. This is the property `env.py` gets from Alembic's default
  single-transaction-per-run on a server with transactional DDL, and until it was
  asserted it was only ever *implied* by a default nobody had written down.
* **TR-004 -- prefix range.** E003 owns `0001`-`0099` and E004 owns `0100`-`0199`.
  A revision numbered outside the block is a collision waiting for the two
  workstreams to merge.
* **TR-002 -- no downgrade body.** Forward-only. A downgrade that *looks*
  plausible and has never been run is worse than none, because it invites
  someone to run it during an incident.

**Discovery, never a hardcoded list.** Every per-revision check parametrizes over
`ScriptDirectory.walk_revisions()`, so the eight revisions still to be written
are covered the moment their files land. A test that named `0001` and `0002`
would pass forever while covering a shrinking fraction of the chain.

**Why this file does not use `db_session`.** `conftest.py` isolates tests by
rolling back an outer transaction, and that fixture is exactly wrong here twice
over. The apply-from-empty test needs a database with *nothing* in it, and the
shared one is at head; and it needs the chain's work to be really committed, so
that a second `upgrade` can observe the recorded version. Both tests that touch
a database therefore create a throwaway one, migrate it, and drop it -- see
`scratch_database`. The shared database is never written to by this module.

**How "a migration ran" is observed.** Through Alembic's documented
`on_version_apply` hook, which `MigrationContext.run_migrations` invokes once per
step, immediately after calling that step's `upgrade()` body. Recording those
callbacks yields the list of revisions that actually executed, which is what
makes "re-application is a no-op" an assertion about work performed rather than
about an exit status. The hook is injected by wrapping
`EnvironmentContext.configure`, because `env.py` -- correctly -- owns the
`configure` call and takes no direction from tests.

**How a mid-run failure is provoked**, for the two VC7 tests. Not by patching a
revision's `upgrade()`: Alembic loads each version file through
`util.load_python_file`, which builds a *fresh* module object per
`ScriptDirectory`, so a `monkeypatch.setattr` on the module reachable from
`CHAIN` is not the module the next `alembic upgrade` will execute -- the sabotage
would silently do nothing and both tests would pass vacuously. Nor by patching
`alembic.op`, which would prove something about the test harness rather than
about the database.

Instead the *database* is made to refuse a statement the chain genuinely issues.
`BLOCKING_RELATION` is created as a table under the name revision `0004` gives to
one of its indexes. Tables and indexes share one relation namespace in
PostgreSQL, so `CREATE INDEX ix_chunk__project ...` fails with SQLSTATE 42P07 --
a real `DuplicateTable` from the server, raised partway through a revision that
has already created a table and one index. That is as close to the shape of a
production migration failure as a test can get, and it exercises PostgreSQL's own
transaction-abort path rather than a simulation of it.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.runtime.environment import EnvironmentContext
from alembic.script import Script, ScriptDirectory
from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from model.schema.cli import DEFAULT_REVISION, EXIT_OK, build_config, main
from model.schema.url import DATABASE_URL_ENV_VAR

#: E003's reserved filename-prefix block (TR-004). E004 owns 0100-0199, and the
#: point of the range check is that a revision numbered into a neighbour's block
#: fails the build here rather than colliding at merge time.
RESERVED_BLOCK_FIRST = 1
RESERVED_BLOCK_LAST = 99

#: Every block {SAD:ADR-0013} assigns, as inclusive `(low, high, owner)` triples.
#:
#: **Added 2026-07-26, and the reason is the same one that rescoped
#: `test_table_ownership.py`.** The range check below asserted that *every*
#: revision in the chain falls inside `0001`-`0099`, which was a faithful
#: reading of TR-004 while E003 was the sole author of this directory. ADR-0013
#: put E004's revisions in the same directory at `0100`-`0103`, so the chain the
#: test walks now contains revisions that are correctly numbered *outside*
#: E003's block, and the assertion failed on the arrangement working as designed.
#:
#: The claim keeps its teeth: a revision numbered `0250` still fails, because it
#: is inside no declared block at all. What it no longer does is treat another
#: epic's correctly-numbered revision as an encroachment.
#:
#: The *partition* claim — that the blocks tile the range without overlap or gap
#: — deliberately does not live here. TR-051 places it at the repository root,
#: in `tests/checks/test_migration_ranges.py`, on the ground that it asserts the
#: boundary *between* two epics' claims and so belongs to neither entry.
DECLARED_BLOCKS: tuple[tuple[int, int, str], ...] = (
    (RESERVED_BLOCK_FIRST, RESERVED_BLOCK_LAST, "E003"),
    (100, 199, "E004"),
)

#: Revision ids are the four-digit prefix itself (see alembic.ini). Exactly four
#: digits: `123` sorts wrong in a directory listing and `00003` is not the
#: convention, so both are defects even though Alembic would accept either.
REVISION_ID_PATTERN = re.compile(r"^\d{4}$")

#: The database `CREATE DATABASE` is issued *from*. It cannot be the database
#: being created, and it must not be the one under test, since PostgreSQL will
#: not drop a database that has a live connection.
MAINTENANCE_DATABASE = "postgres"

#: Scratch database names are built, not taken from input, and are matched
#: against this before being interpolated into DDL. PostgreSQL has no bind
#: parameter for an identifier, so the guard is what stands in for one.
SCRATCH_DATABASE_PREFIX = "e003_migration_chain_"
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9_]+$")

#: Alembic's bookkeeping table. Probed by name because the VC7 tests need to
#: distinguish "it says nothing" from "it is not there at all", and reading
#: `version_num` cannot tell those apart -- the first raises `UndefinedTable`.
ALEMBIC_VERSION_TABLE = "alembic_version"

#: The revision the two VC7 tests make fail, and the relation name they collide
#: with to do it. `0004` creates `chunk`, then `ix_chunk__document_page`, then
#: `ix_chunk__project` -- so a pre-existing relation under that third name stops
#: the revision *after* it has already emitted DDL, which is what "partway
#: through" has to mean for the test to be about anything.
#:
#: Naming a revision here is a deliberate coupling and it is a checked one:
#: `_revisions_before` fails loudly if `0004` ever leaves the chain, rather than
#: letting the sabotage quietly stop working.
BLOCKED_REVISION = "0004"
BLOCKING_RELATION = "ix_chunk__project"

#: Created by `0004` immediately *before* the statement that fails. Its absence
#: after the failed run is the evidence that the dead revision's own partial work
#: was undone, as distinct from the revision never having started.
PARTIAL_WORK_OF_BLOCKED_REVISION = "ix_chunk__document_page"

#: Spelled out rather than built from `BLOCKING_RELATION` at the call site. SQL
#: assembled from values is what Ruff S608 exists to catch, and a literal costs
#: nothing here -- the identifier is a constant either way.
CREATE_BLOCKING_RELATION = 'CREATE TABLE "ix_chunk__project" (blocked boolean)'
DROP_BLOCKING_RELATION = 'DROP TABLE "ix_chunk__project"'

#: One relation per revision that creates one, oldest first. This is the "objects
#: actually present" half of VC7: after a run stops at revision R, exactly the
#: entries at or before R must exist and no others.
#:
#: `0001` and `0009` are absent on purpose rather than by oversight -- `0001`
#: creates only the `vector` extension and `0009` only roles and grants, neither
#: of which is a relation in `pg_class`.
RELATION_CREATED_BY: dict[str, str] = {
    "0002": "schema_constants",
    "0003": "document",
    "0004": "chunk",
    "0005": "field_vocabulary",
    "0006": "extracted_value",
    "0007": "purchase_order_line",
    "0008": "forecast_run",
    "0010": "resolved_entity",
}

#: Everything the VC7 tests look for in one query, so a comparison is against a
#: whole set and never against one name at a time. `BLOCKING_RELATION` is in the
#: list so that every assertion has at least one relation it expects to *find*:
#: a probe that can only ever return the empty set would pass just as well if the
#: query itself were broken.
PROBED_RELATIONS: tuple[str, ...] = (
    ALEMBIC_VERSION_TABLE,
    *RELATION_CREATED_BY.values(),
    PARTIAL_WORK_OF_BLOCKED_REVISION,
    BLOCKING_RELATION,
)

#: Name and kind of each probed relation that exists. `relkind` is carried
#: because it is what distinguishes the blocking *table* from the real *index*
#: `0004` creates under the same name once the block is removed.
PRESENT_RELATIONS = text(
    """
    SELECT c.relname, c.relkind
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = ANY(:names)
    """
)

#: `pg_class.relkind` values this module asserts on.
RELKIND_TABLE = "r"
RELKIND_INDEX = "i"


def _discover_chain() -> tuple[Script, ...]:
    """Every revision on disk, oldest first.

    `walk_revisions()` yields newest-first from the head down; reversing gives
    application order, which is what the apply-from-empty test compares against.
    Ordering comes from `down_revision` links and never from the numeric prefix,
    so a chain whose numbering is wrong still reports its real order here and
    fails in the prefix test instead -- one defect, one failure.

    Resolved through `build_config()` -- the same function the `migrate` console
    entry point uses -- so the tests cannot disagree with the runner about which
    directory holds the chain.
    """
    return tuple(reversed(tuple(ScriptDirectory.from_config(build_config()).walk_revisions())))


#: Collected once at import because `pytest.mark.parametrize` needs the list at
#: collection time. Everything derived from it is derived, never restated.
CHAIN: tuple[Script, ...] = _discover_chain()
CHAIN_REVISION_IDS: tuple[str, ...] = tuple(script.revision for script in CHAIN)


@contextmanager
def _recording_applied_revisions() -> Iterator[list[str]]:
    """Record the revision id of every migration that actually executes.

    Yields a list that fills as the chain runs. `MigrationContext.run_migrations`
    calls each registered `on_version_apply` callback once per step, after that
    step's `upgrade()` body has returned, so an entry in this list means the
    body ran -- not that Alembic considered it, and not that the command
    succeeded.

    `EnvironmentContext.configure` is wrapped rather than `env.py` being altered
    to read a test hook. `env.py` is production code whose job is to configure
    the migration context for a real run; a branch in it that exists only for
    tests is a branch that can drift from the path CI exercises. The wrapper
    appends to any `on_version_apply` the environment may itself set, and is
    removed unconditionally on the way out so that no later test in the session
    inherits it.
    """
    applied: list[str] = []

    def record(*, step: Any, **_unused: Any) -> None:
        # `step` is Alembic's MigrationInfo. The other keyword arguments the hook
        # is called with (ctx, heads, run_args) are absorbed rather than named,
        # so this stays compatible if the hook's signature grows.
        #
        # Stamps move the version pointer without running a body; only genuine
        # migrations count as work performed.
        if step.is_migration and step.up_revision_id is not None:
            applied.append(step.up_revision_id)

    original_configure = EnvironmentContext.configure

    def configure(self: EnvironmentContext, *args: Any, **kwargs: Any) -> None:
        existing = kwargs.get("on_version_apply")
        if existing is None:
            callbacks: list[Any] = []
        elif callable(existing):
            callbacks = [existing]
        else:
            callbacks = list(existing)
        kwargs["on_version_apply"] = [*callbacks, record]
        original_configure(self, *args, **kwargs)

    EnvironmentContext.configure = configure  # type: ignore[method-assign]
    try:
        yield applied
    finally:
        EnvironmentContext.configure = original_configure  # type: ignore[method-assign]


def _upgrade_to_head() -> list[str]:
    """Run `alembic upgrade head` and return the revisions that executed.

    Drives `alembic.command.upgrade` against a freshly loaded config, which is
    the same call the `migrate` console entry point makes. The target database is
    whatever `DATABASE_URL` names at this moment -- the `scratch_database`
    fixture is what points it somewhere disposable.
    """
    with _recording_applied_revisions() as applied:
        command.upgrade(build_config(), "head")
    return applied


def _migrate(revision: str = DEFAULT_REVISION) -> list[str]:
    """Run the `migrate` console entry point and return the revisions that executed.

    The entry point, not `alembic.command.upgrade`, because VC7's second half is
    that "re-applying the sequence advances from that revision without manual
    repair" -- and the sequence as anyone actually re-applies it is
    `uv run --directory src/model migrate`. Driving `main()` proves the recovery
    path a human or a CI step would take, exit code included; calling `command`
    directly would leave the entry point's own argument handling and exit-code
    contract out of the claim.

    Asserts `EXIT_OK` here rather than at each call site: every caller of this
    helper expects the run to succeed, and a run that returned `EXIT_FAILED`
    would otherwise be read as "no migration executed" by the list comparison
    that follows, which is the same observation a genuine no-op produces.
    """
    with _recording_applied_revisions() as applied:
        exit_code = main([revision])

    assert exit_code == EXIT_OK, (
        f"`migrate {revision}` exited {exit_code}, not {EXIT_OK}. The revisions it managed "
        f"to apply first were {applied}; whatever is asserted below about the list of "
        f"applied revisions would be measuring a failed run."
    )
    return applied


def _revisions_before(revision: str) -> list[str]:
    """The chain up to but excluding `revision`, in application order."""
    assert revision in CHAIN_REVISION_IDS, (
        f"revision {revision!r} is not in the chain {list(CHAIN_REVISION_IDS)}. The two "
        f"OBJ1 VC7 tests provoke a failure by colliding with a relation that revision "
        f"creates, so the sabotage no longer sabotages anything and those tests would "
        f"pass without having failed a migration. Point BLOCKED_REVISION and "
        f"BLOCKING_RELATION at a revision that exists."
    )
    return list(CHAIN_REVISION_IDS[: CHAIN_REVISION_IDS.index(revision)])


def _revisions_from(revision: str) -> list[str]:
    """The chain from `revision` to the head, in application order."""
    _revisions_before(revision)  # same membership check, same failure message
    return list(CHAIN_REVISION_IDS[CHAIN_REVISION_IDS.index(revision) :])


def _relations_expected_after(revision: str) -> set[str]:
    """The probed relations that must exist once the chain has run through `revision`.

    Alembic's own bookkeeping table is included because a database that has
    completed any revision has been stamped, and a stamped version with no
    version table is exactly the disagreement VC7 is about.
    """
    applied = {*_revisions_before(revision), revision}
    return {ALEMBIC_VERSION_TABLE} | {
        relation for rev, relation in RELATION_CREATED_BY.items() if rev in applied
    }


def _execute_committed(url: URL, statement: str) -> None:
    """Run one statement against `url` and commit it, on a connection of its own.

    Used to place and remove the blocking relation. Committed, and therefore
    outside anything Alembic will roll back: the point of the two VC7 tests is
    that the migration run's transaction is discarded, so the obstacle has to
    outlive that rollback or the recovery run would succeed for the wrong reason.
    `NullPool` and an immediate dispose for the reason `_stamped_revisions`
    gives -- the caller may be about to drop this database.
    """
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()


def _present_relations(url: URL, names: Sequence[str]) -> dict[str, str]:
    """Which of `names` exist in `public` at `url`, mapped to their `relkind`.

    Read from `pg_class` rather than `information_schema.tables`, which lists no
    indexes at all -- and one of the names asked about is an index, both before
    and after the chain recreates it.

    `names` is bound as an array parameter, so no identifier is interpolated into
    SQL and a name that does not exist is simply absent from the result rather
    than an error.
    """
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            rows = connection.execute(PRESENT_RELATIONS, {"names": list(names)}).all()
    finally:
        engine.dispose()
    return {row.relname: row.relkind for row in rows}


def _stamped_revisions(url: URL) -> set[str]:
    """The contents of `alembic_version` in the database at `url`.

    A separate short-lived engine, disposed immediately: the caller is usually
    about to drop this database, and a pooled connection left open would make
    `DROP DATABASE` fail for a reason that has nothing to do with the test.
    """
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return set(
                connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
            )
    finally:
        engine.dispose()


@pytest.fixture
def scratch_database(database_url: URL, monkeypatch: pytest.MonkeyPatch) -> Iterator[URL]:
    """An empty database of its own, created for one test and dropped after it.

    Function-scoped and uniquely named, so the two tests that use it each get a
    database with nothing in it and cannot observe each other's migrations. The
    cost is one `CREATE DATABASE` per test, which copies `template1` and is
    cheap; sharing one instead would make "empty" depend on execution order.

    `DATABASE_URL` is repointed at the new database for the duration, because
    that variable is the *only* channel `env.py` reads -- steering the run any
    other way would test a code path the deployed runner does not use.
    `monkeypatch` restores the original value even if the test fails, so the
    shared database named by the developer's environment is never touched.

    The connection target, credentials, host, and port are inherited from
    `conftest.py`'s `database_url`, so nothing is hardcoded and the whole tier
    still skips cleanly when `DATABASE_URL` is unset.

    Teardown drops the database `WITH (FORCE)`, terminating any connection that
    outlived its test. Without it, one leaked connection would leave a stray
    database behind on every subsequent run.
    """
    name = f"{SCRATCH_DATABASE_PREFIX}{uuid4().hex}"
    if not SAFE_IDENTIFIER_PATTERN.fullmatch(name):
        raise AssertionError(f"refusing to build DDL around the identifier {name!r}")

    # AUTOCOMMIT because PostgreSQL runs neither CREATE DATABASE nor DROP
    # DATABASE inside a transaction block.
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


def test_chain_resolves_to_exactly_one_head() -> None:
    """TR-005: the revision graph has a single tip.

    Two heads is what parallel authoring produces -- two revisions naming the
    same `down_revision` -- and `alembic upgrade head` then fails outright rather
    than picking one. The fix is an explicit merge revision, not a rename, which
    is why this failure needs to surface in CI and not on a deployment host.
    """
    assert CHAIN, (
        "no revisions were discovered at all. The chain is resolved through "
        "model.schema.cli.build_config(); an empty result means alembic.ini's "
        "version_locations no longer points at the versions directory."
    )

    heads = ScriptDirectory.from_config(build_config()).get_heads()

    assert len(heads) == 1, (
        f"the chain resolves to {len(heads)} heads ({sorted(heads)}), so "
        f"`alembic upgrade head` has no single destination and will refuse to run. "
        f"Two revisions share a down_revision; reconcile them with an explicit merge "
        f"revision rather than by renumbering."
    )


@pytest.mark.parametrize("script", CHAIN, ids=CHAIN_REVISION_IDS)
def test_revision_id_falls_inside_the_reserved_block(script: Script) -> None:
    """TR-004/TR-005: every revision id is four digits inside a declared block.

    The revision id *is* the filename prefix in this project (alembic.ini's
    `file_template` is `%(rev)s_%(slug)s`), so checking the id checks both.

    **Rescoped 2026-07-26 from "inside E003's block" to "inside a declared
    block".** ADR-0013 makes this one directory serve two epics, so the chain
    this test walks contains E004's `0100`-`0103` — correctly numbered, in the
    block reserved for them, and previously reported here as an encroachment.
    See `DECLARED_BLOCKS` for the full reasoning and for why the partition claim
    lives at the repository root instead.
    """
    assert REVISION_ID_PATTERN.match(script.revision), (
        f"revision id {script.revision!r} is not a four-digit prefix. "
        f"Create revisions with `alembic -c alembic.ini revision --rev-id 00NN -m '...'` "
        f"so the id and the filename prefix stay the same string."
    )

    number = int(script.revision)
    owner = next(
        (owner for low, high, owner in DECLARED_BLOCKS if low <= number <= high), None
    )

    assert owner is not None, (
        f"revision {script.revision!r} is outside every declared block (TR-004). "
        f"Declared: "
        f"{', '.join(f'{low:04d}-{high:04d} {name}' for low, high, name in DECLARED_BLOCKS)}. "
        f"Nothing but the prefix says which epic owns a revision, so a number "
        f"outside the blocks belongs to no one; renumber it into its epic's block."
    )


@pytest.mark.parametrize("script", CHAIN, ids=CHAIN_REVISION_IDS)
def test_filename_carries_the_revision_as_its_prefix(script: Script) -> None:
    """TR-004: the file on disk is named for the revision it contains.

    The prefix is a labelling convention -- ordering is `down_revision` and only
    `down_revision` -- but a file whose name disagrees with its revision id
    defeats the convention's entire purpose, which is that a directory listing
    reads as the chain.
    """
    filename = Path(script.path).name

    assert filename.startswith(f"{script.revision}_"), (
        f"{filename!r} holds revision {script.revision!r} but is not named for it. "
        f"Rename the file to {script.revision}_<slug>.py."
    )


def test_chain_is_linear() -> None:
    """TR-005: one base, single-parent revisions, and no branch points.

    Linearity is asserted three ways because each catches a different mistake: a
    second `down_revision = None` is a second root that silently never runs; a
    tuple-valued `down_revision` is a merge, which only exists because someone
    already created two heads; and a branch point is two revisions claiming the
    same parent, which is the multi-head defect one commit before it becomes
    visible.
    """
    bases = [script.revision for script in CHAIN if script.down_revision is None]

    assert len(bases) == 1, (
        f"expected exactly one base revision (down_revision = None) but found {bases}. "
        f"Every revision after the first must name its parent."
    )

    for script in CHAIN:
        if script.down_revision is None:
            continue
        assert isinstance(script.down_revision, str), (
            f"revision {script.revision!r} has multiple parents "
            f"({script.down_revision!r}), so it is a merge revision. Merges exist only "
            f"to reconcile divergent heads, which this chain is not supposed to have."
        )

    branch_points = [script.revision for script in CHAIN if script.is_branch_point]

    assert not branch_points, (
        f"revisions {branch_points} are branch points -- more than one revision names "
        f"each as its parent. That is a multi-head chain; reconcile the branches."
    )


@pytest.mark.parametrize("script", CHAIN, ids=CHAIN_REVISION_IDS)
def test_downgrade_refuses_instead_of_reversing(script: Script) -> None:
    """TR-002: calling `downgrade()` raises.

    Calling it is the strong form of this check: a stub that raises and a stub
    that quietly returns are indistinguishable by inspection of the file's
    length, and only one of them keeps a half-reversed schema off a production
    database.

    `NotImplementedError` specifically, not any exception. A `downgrade()` that
    *did* carry operations would also raise when called outside a migration run
    -- Alembic's `op` proxy raises `NameError` until a migration context is
    established -- so accepting any exception would let a real downgrade body
    pass this test.
    """
    with pytest.raises(NotImplementedError):
        script.module.downgrade()


@pytest.mark.parametrize("script", CHAIN, ids=CHAIN_REVISION_IDS)
def test_downgrade_body_contains_no_operations(script: Script) -> None:
    """TR-002: no `op.*` call appears in a `downgrade()` body, reachable or not.

    The paired static check to the one above, and it catches what calling cannot:
    operations written *after* the raise. Those never execute, so the call-based
    test stays green, but TR-002's wording is that migrations contain no reverse
    operations -- and dead reversal code is an invitation to delete the raise and
    "just run it" during an incident.
    """
    source = Path(script.path).read_text(encoding="utf-8")
    downgrade = next(
        (
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        ),
        None,
    )

    assert downgrade is not None, (
        f"{Path(script.path).name} defines no downgrade(). Alembic calls that attribute "
        f"when a downgrade is requested, so a missing one fails with an unexplained "
        f"AttributeError instead of stating the forward-only policy (TR-002)."
    )

    operations = [
        node.func.attr
        for node in ast.walk(downgrade)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    ]

    assert not operations, (
        f"downgrade() in {Path(script.path).name} calls {operations} on the Alembic "
        f"operations proxy. Migrations here are forward-only (TR-002): the body must "
        f"raise and nothing else. To undo a schema change, author a new forward revision."
    )


def test_chain_applies_to_an_empty_database(scratch_database: URL) -> None:
    """TR-001: `upgrade head` against a database with nothing in it.

    Run against a scratch database rather than the shared one for the reason the
    module docstring gives: the shared database is already at head, so it would
    prove nothing, and it carries objects a fresh deployment will not have. This
    is the test that catches a revision depending on something an earlier one
    never created -- an extension, a type, a table -- which is invisible on any
    database that has been migrated incrementally.

    Every revision on disk is asserted to have run, in the order the chain
    defines. Comparing against the discovered order rather than a sorted list of
    ids keeps the two failures separate: a badly numbered revision fails the
    prefix test, not this one.
    """
    applied = _upgrade_to_head()

    assert applied == list(CHAIN_REVISION_IDS), (
        f"the chain executed {applied} but the revisions on disk are "
        f"{list(CHAIN_REVISION_IDS)}. A revision that exists but did not run is "
        f"unreachable from the base -- check its down_revision."
    )

    heads = set(ScriptDirectory.from_config(build_config()).get_heads())

    assert _stamped_revisions(scratch_database) == heads, (
        "the migrated database's alembic_version does not record the chain's head, so "
        "a later `upgrade head` would try to re-run migrations that have already run."
    )


def test_reapplying_at_head_runs_no_migration(scratch_database: URL) -> None:
    """TR-003: a second `upgrade head` performs no work.

    Two upgrades in one test, on one scratch database, because the claim is about
    the *second* one and it only means something if the first is known to have
    done something. Exit status is not the assertion: `alembic upgrade head`
    exits zero whether it applied ten migrations or none, so this asserts on the
    list of migration bodies that actually executed -- which must be empty.

    Idempotence here is Alembic's `alembic_version` bookkeeping doing its job.
    That is deliberate and is the reason TR-003 forbids a migration body from
    carrying its own "have I run yet?" guard: such a guard would make this test
    pass while the migration was in fact re-entered every time.
    """
    first_run = _upgrade_to_head()

    assert first_run == list(CHAIN_REVISION_IDS), (
        f"setup failed: the first upgrade applied {first_run}, not the whole chain "
        f"{list(CHAIN_REVISION_IDS)}. There is nothing to re-apply, so the idempotence "
        f"claim below would hold vacuously."
    )
    stamped_after_first_run = _stamped_revisions(scratch_database)

    second_run = _upgrade_to_head()

    assert second_run == [], (
        f"re-running `upgrade head` re-executed {second_run}. Re-application must run "
        f"no migration already applied (TR-003); a migration that runs twice is one "
        f"DROP or one INSERT away from destroying data on a redeploy."
    )
    assert _stamped_revisions(scratch_database) == stamped_after_first_run, (
        "alembic_version changed during a no-op upgrade, so the version bookkeeping "
        "does not agree with the work performed."
    )


def test_a_run_that_fails_partway_leaves_none_of_its_own_work_behind(
    scratch_database: URL,
) -> None:
    """TR-001, OBJ1 VC7: one failed run against an empty database changes nothing.

    This is the atomicity half of VC7 and it is the half that depends on a
    default nobody wrote down: `env.py` wraps the whole run in a single
    `context.begin_transaction()` and does *not* pass
    `transaction_per_migration=True`, so on PostgreSQL -- where DDL is
    transactional -- either the entire chain lands or none of it does.

    The assertion that makes this a real test rather than a restatement is the
    pair. Three revisions are observed to have **executed**, through the
    `on_version_apply` hook, and then every object they created is observed to be
    **absent**. Work performed and work persisted are different quantities, and
    only a test that measures both can tell a single-transaction run from a
    per-migration one; the per-migration configuration would leave `document` and
    `schema_constants` sitting in a database stamped at `0003`.

    Nothing is asserted about how far the run got beyond that -- the point is not
    that `0004` is where it stops, but that wherever it stops, the database has
    not been half-changed.
    """
    _execute_committed(scratch_database, CREATE_BLOCKING_RELATION)

    with _recording_applied_revisions() as executed, pytest.raises(DBAPIError) as failure:
        main([DEFAULT_REVISION])

    assert isinstance(failure.value.orig, psycopg.errors.DuplicateTable), (
        f"the run was expected to die on `CREATE INDEX {BLOCKING_RELATION}` colliding with "
        f"the blocking table of the same name (SQLSTATE 42P07), but it raised "
        f"{type(failure.value.orig).__name__} "
        f"(SQLSTATE {getattr(failure.value.orig, 'sqlstate', None)}). The migration failed "
        f"for some other reason, so what follows is not measuring a mid-run failure."
    )
    assert executed == _revisions_before(BLOCKED_REVISION), (
        f"setup failed: {executed} executed before the failure, not "
        f"{_revisions_before(BLOCKED_REVISION)}. If that list is empty the run never "
        f"reached any migration and the rollback below has nothing to undo, so the "
        f"atomicity claim would hold vacuously."
    )

    present = _present_relations(scratch_database, PROBED_RELATIONS)

    assert set(present) == {BLOCKING_RELATION}, (
        f"after a failed run the only relation that may exist is the blocking table this "
        f"test created; found {sorted(present)}. Every other name here was created by a "
        f"revision that ran and must have been rolled back with the run's single "
        f"transaction. {ALEMBIC_VERSION_TABLE!r} among them means the version pointer "
        f"survived work that did not -- which is precisely the state OBJ1 VC7 forbids."
    )

    _execute_committed(scratch_database, DROP_BLOCKING_RELATION)
    recovered = _migrate()

    assert recovered == list(CHAIN_REVISION_IDS), (
        f"with the obstacle removed, `migrate` applied {recovered} rather than the whole "
        f"chain {list(CHAIN_REVISION_IDS)}. Recovery from a failed run must need no manual "
        f"repair -- a chain that now needs a stamp or a hand-dropped object has left the "
        f"database in a state the runner cannot reason about."
    )
    assert _stamped_revisions(scratch_database) == set(
        ScriptDirectory.from_config(build_config()).get_heads()
    )


def test_a_failed_run_leaves_the_version_table_agreeing_with_the_objects_present(
    scratch_database: URL,
) -> None:
    """TR-003, OBJ1 VC7: a failure over a *committed* prefix resumes from that revision.

    The test above starts from empty, so its "consistent" state is the empty one.
    This one starts from a database that genuinely completed part of the chain in
    an earlier, committed run -- which is the state a real deployment is in when
    the next release's migration fails -- and asserts the two halves of VC7
    separately:

    * **The recorded revision matches the objects actually present.**
      `alembic_version` still reads the last committed revision, the relations
      that revision and its predecessors created are all there, and *nothing* the
      dead revision created is -- including `PARTIAL_WORK_OF_BLOCKED_REVISION`,
      the index `0004` creates one statement before the one that fails. Asserting
      that name specifically is what separates "the revision was rolled back"
      from "the revision never began".
    * **Re-applying advances from that revision.** Not "ends at head" -- that
      would also be true of a run that started over and re-executed the committed
      prefix, which is the failure mode TR-003 exists to catch and which would
      mean a redeploy re-running every `CREATE` in the chain. The assertion is on
      the exact list, so the resumed run must execute the tail and only the tail.

    The blocking relation gets a second look on the way out. It goes in as a
    table and comes back as an index, because `0004` recreates the name once the
    obstacle is gone; that transition is the evidence the resumed run really
    re-issued the statement that had failed rather than skipping past it.
    """
    last_committed = _revisions_before(BLOCKED_REVISION)[-1]
    committed = _migrate(last_committed)

    assert committed == _revisions_before(BLOCKED_REVISION), (
        f"setup failed: `migrate {last_committed}` applied {committed}, not "
        f"{_revisions_before(BLOCKED_REVISION)}. There is then no committed prefix for the "
        f"failed run to be measured against."
    )

    _execute_committed(scratch_database, CREATE_BLOCKING_RELATION)

    with _recording_applied_revisions() as executed, pytest.raises(DBAPIError) as failure:
        main([DEFAULT_REVISION])

    assert isinstance(failure.value.orig, psycopg.errors.DuplicateTable), (
        f"the run was expected to die on `CREATE INDEX {BLOCKING_RELATION}` (SQLSTATE "
        f"42P07) but raised {type(failure.value.orig).__name__} "
        f"(SQLSTATE {getattr(failure.value.orig, 'sqlstate', None)})."
    )
    assert executed == [], (
        f"{executed} completed during the failed run. {BLOCKED_REVISION} is the first "
        f"revision it had left to apply and it is the one that fails, so no migration "
        f"should have reached the point of being recorded as applied."
    )

    assert _stamped_revisions(scratch_database) == {last_committed}, (
        f"after the failed run alembic_version must still read {last_committed!r} -- the "
        f"last revision that committed. A pointer that moved to {BLOCKED_REVISION!r} "
        f"anyway would make the next run skip a revision whose objects do not exist, and "
        f"the schema would be permanently short of them with nothing reporting it."
    )

    present = _present_relations(scratch_database, PROBED_RELATIONS)

    assert set(present) == _relations_expected_after(last_committed) | {BLOCKING_RELATION}, (
        f"the objects present disagree with the recorded revision {last_committed!r}. "
        f"Found {sorted(present)}; expected "
        f"{sorted(_relations_expected_after(last_committed) | {BLOCKING_RELATION})}. A "
        f"missing name means committed work was lost; an extra one means the failed "
        f"revision left an object behind that the version table does not account for, and "
        f"the next run will fail on it forever."
    )
    assert present[BLOCKING_RELATION] == RELKIND_TABLE, (
        f"{BLOCKING_RELATION!r} should still be the blocking table this test created; "
        f"relkind is {present[BLOCKING_RELATION]!r}."
    )

    _execute_committed(scratch_database, DROP_BLOCKING_RELATION)
    resumed = _migrate()

    assert resumed == _revisions_from(BLOCKED_REVISION), (
        f"the resumed run executed {resumed}; it must execute exactly "
        f"{_revisions_from(BLOCKED_REVISION)} -- the tail from the failed revision "
        f"onwards, and nothing already committed. Re-running the committed prefix would "
        f"mean every redeploy after a failed migration re-issues the whole chain."
    )
    assert _stamped_revisions(scratch_database) == set(
        ScriptDirectory.from_config(build_config()).get_heads()
    )

    recovered = _present_relations(scratch_database, PROBED_RELATIONS)

    assert set(recovered) == set(PROBED_RELATIONS), (
        f"after recovery every probed relation must exist; missing "
        f"{sorted(set(PROBED_RELATIONS) - set(recovered))}."
    )
    assert recovered[BLOCKING_RELATION] == RELKIND_INDEX, (
        f"{BLOCKING_RELATION!r} is a {recovered[BLOCKING_RELATION]!r}, not an index. The "
        f"resumed run has to re-issue the CREATE INDEX that failed; if the name is still "
        f"a table, {BLOCKED_REVISION} was skipped rather than applied."
    )
