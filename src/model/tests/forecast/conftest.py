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

**There is an `__init__.py` in this directory, and an earlier revision of this
paragraph said there deliberately was not.** The original arrangement followed
`tests/schema` and `tests/procurement`, neither of which carries one: pytest's
default import mode puts each test directory on `sys.path` and distinguishes
modules by basename, so a package marker looked like a change to how three
sibling suites are imported in order to solve a problem none of them had. The
stated cost was that test module basenames in this tier stay unique across
`src/model/tests`. **That cost was not paid** — this tier's
`test_serialize_properties.py` collides with `tests/procurement`'s, and the
collision is invisible for exactly as long as one of the two fails to import.
It surfaced the moment `model.forecast.serialize` landed and the E007 module
started importing cleanly, as `import file mismatch` aborting collection for the
whole entry rather than as a failure in either tier.

The marker is the remedy rather than a rename because every path this tier is
referenced by — `tasks.md`'s red-green pairs among them — names the file as it
is. Only this directory is marked, so the two sibling suites are imported
exactly as they were; what changes is that modules here are imported as
`forecast.<name>` from `src/model/tests` on `sys.path`, which is what makes the
basename local to the tier instead of global to the entry.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.orm import Session

from model.forecast.config import CHAINS, DRAWS_PER_CHAIN, TUNING_DRAWS_PER_CHAIN
from model.forecast.manifest import RunManifest
from model.forecast.model import SojournFrame, training_frame
from model.forecast.read import ProcurementInput, read_lines_and_events
from model.forecast.serialize import input_data_hash
from model.forecast.shrinkage import VendorShrinkage
from model.forecast.split import TRAIN, SplitAssignment, SplitResult, assign_split
from model.forecast.write import LinePosteriorRow
from model.procurement.load import load
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


# --------------------------------------------------------------------------
# One real `forecast-fit`, emitted once and shared by the whole tier
# --------------------------------------------------------------------------

#: The anchor every run this tier emits is fitted at. A committed date and never
#: a clock read, for the reason `fit.py` refuses to default one: a clock would
#: move every open line's elapsed time between two otherwise identical runs, and
#: the elapsed time is what half the assertions in this tier are about.
FIT_AS_OF_DATE = date(2026, 4, 1)

#: The shared run's root entropy. Recorded verbatim in `forecast_run
#: .seed_entropy`, so a test needing the fit's own posterior re-derives it from
#: the stored provenance rather than from this constant.
FIT_SEED_ENTROPY = 20260728

#: The shape the shared run is emitted at — `config.py`'s own committed values,
#: whose product is the `schema_constants.draw_count` DV-014 pins. Deliberately
#: **not** a tiny fit: a run at two chains of fifty draws records 100 draws, and
#: DV-014 is unassertable against a run whose realized shape is not the
#: committed one. It costs about half a minute, once per session, and every
#: assertion over stored rows in this tier reads the result.
FIT_CHAINS = CHAINS
FIT_DRAWS_PER_CHAIN = DRAWS_PER_CHAIN
FIT_TUNING_DRAWS = TUNING_DRAWS_PER_CHAIN

#: The console entry point declared in `src/model/pyproject.toml`. Invoked as a
#: real process rather than by calling `main()` in-process, because DV-042 is a
#: claim about a process's two streams and its exit status — none of which an
#: in-process call produces.
CONSOLE_SCRIPT_NAME = "forecast-fit"

#: Module-level SQL, never assembled from values (Ruff S608).
LOADED_LINE_COUNT_SQL = text("SELECT count(*) FROM purchase_order_line")
ALL_RUN_IDS_SQL = text("SELECT run_id FROM forecast_run")
DISCARD_RUN_SQL = text("DELETE FROM forecast_run WHERE run_id = :run_id")
STORED_RUN_SQL = text("SELECT * FROM forecast_run WHERE run_id = :run_id")
STORED_POSTERIOR_SQL = text(
    """
    SELECT po_line_id, draws, survival, residual_tail_mass, draw_digest
    FROM line_posterior WHERE run_id = :run_id ORDER BY po_line_id
    """
)
STORED_ASSIGNMENT_SQL = text(
    """
    SELECT a.po_line_id, l.project_id, l.po_number, l.line_number,
           a.split_side, a.is_censored, a.canonical_ordinal
    FROM forecast_split_assignment a
    JOIN purchase_order_line l ON l.po_line_id = a.po_line_id
    WHERE a.run_id = :run_id ORDER BY a.canonical_ordinal
    """
)


@dataclass(frozen=True, slots=True)
class EmittedRun:
    """What one invocation of `forecast-fit` produced, streams included.

    The `run_id` is the value the job put on standard output, parsed rather than
    read back out of the database — DV-014 and DV-042 both scope their claims to
    "the run this invocation returned", never to every row in `forecast_run`,
    because E003's delivered fixtures land legally in the same table.
    """

    run_id: uuid.UUID
    stdout: str
    stderr: str
    status: int
    argv: tuple[str, ...]
    report_root: Path
    as_of_date: date
    seed_entropy: int
    chain_count: int
    draws_per_chain: int


# `eq=False` because `SojournFrame` holds arrays and a generated `__eq__` would
# compare elementwise, yielding an array where a bool is expected.
@dataclass(frozen=True, slots=True, eq=False)
class FitInput:
    """The shared run's input, re-derived from the schema without sampling.

    Everything `fit.py` computes before it calls the sampler: the rows, the digest
    over them, the split keyed on that digest, the roster, and the training frame
    the design matrix was built from. All of it is deterministic and none of it
    costs a fit, which is what lets a test compare a recorded field against the
    measurement it claims to be rather than against a second label.
    """

    procurement_input: ProcurementInput
    input_data_hash: str
    split: SplitResult
    vendor_ids: tuple[str, ...]
    material_categories: tuple[str, ...]
    frame: SojournFrame
    training_line_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class StoredRun:
    """One emitted run read back out of the schema, in the writer's own types.

    The point of the round trip is that a test re-emitting a *variant* of this
    run — at another shape, or with another shrinkage object — drives the real
    `write.py` statements against values the database actually holds, rather
    than against a second set assembled beside them.
    """

    manifest: RunManifest
    line_posteriors: tuple[LinePosteriorRow, ...]
    assignments: tuple[SplitAssignment, ...]


def console_script_path() -> Path:
    """Where this environment's `forecast-fit` executable is.

    Resolved beside `sys.executable` first, so the script that runs is the one
    belonging to the interpreter running the tests, and only then through
    `PATH`. Both, rather than either: `uv run` puts the entry's scripts on
    `PATH`, and a bare `pytest` under an activated environment may not.
    """
    suffix = ".exe" if os.name == "nt" else ""
    candidate = Path(sys.executable).parent / f"{CONSOLE_SCRIPT_NAME}{suffix}"
    if candidate.exists():
        return candidate
    located = shutil.which(CONSOLE_SCRIPT_NAME)
    if located is None:
        raise pytest.UsageError(
            f"the {CONSOLE_SCRIPT_NAME!r} console entry point is not installed in this "
            f"environment, so this tier cannot invoke the job it exists to assert on. "
            f"Install the entry with `uv sync --directory src/model`."
        )
    return Path(located)


def _invoke(arguments: list[str], environment: dict[str, str] | None = None):
    """Run `forecast-fit` and capture both streams and the exit status.

    Never `shell=True` and never a string command line: the argument vector is
    fixed here and the values in it come from this tier's own constants.
    `check=False` because a refusal's non-zero status is the measurement rather
    than an error.
    """
    return subprocess.run(  # noqa: S603 - fixed argv from this module's constants
        [str(console_script_path()), *arguments],
        capture_output=True,
        text=True,
        check=False,
        env=dict(os.environ) if environment is None else environment,
    )


def discard_run(engine: Engine, run_id: uuid.UUID) -> None:
    """Remove one committed run and everything keyed to it.

    Every artifact store cascades from `forecast_run`, so the single delete is
    the whole cleanup. This tier leaves `forecast_run` **empty**: a committed
    active run breaks E003's single-active-run fixture and migration `0300`'s
    empty-table guard, so a run this tier commits is a run this tier removes.
    """
    with engine.begin() as connection:
        connection.execute(DISCARD_RUN_SQL, {"run_id": run_id})


@pytest.fixture(scope="session")
def forecast_fit(schema_at_head: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Invoke `forecast-fit` as a process, returning the completed invocation.

    Handed to tests as a callable rather than as a result, because DV-042's
    three clauses are about three different invocations — one that ships and two
    that refuse — and only the caller knows which arguments produce which.
    """
    del schema_at_head  # requested for its assertion, not for its value
    return _invoke


@pytest.fixture(scope="package")
def committed_dataset(engine: Engine, schema_at_head: str) -> int:
    """The committed procurement dataset, present in the schema before any fit.

    FR-001 has the fit read `purchase_order_line` and `lifecycle_event` and never
    the fixture file, so a run needs the dataset *loaded* — and the database this
    tier meets is not always one that holds it. A fresh CI database has only the
    migration chain applied, and a full `src/model` run leaves the table empty,
    because `tests/procurement` empties it in teardown by design.

    So the precondition is **established** rather than asserted, through the same
    `load()` the `procurement-load` console entry calls. Loading rather than
    refusing, because the alternative is a tier that is red on exactly the
    environment it is meant to run in; refusing rather than skipping if `load()`
    itself declines, because a divergent database is a different problem and it
    names itself.
    """
    del schema_at_head  # requested for its assertion, not for its value
    with engine.connect() as connection:
        loaded = int(connection.execute(LOADED_LINE_COUNT_SQL).scalar_one())
    if loaded:
        return loaded

    outcome = load(url=engine.url)
    with engine.connect() as connection:
        loaded = int(connection.execute(LOADED_LINE_COUNT_SQL).scalar_one())

    assert loaded, (
        f"`purchase_order_line` is still empty after loading the committed dataset "
        f"({outcome}); the fit reads the schema and never the fixture file, so there is "
        f"nothing for this tier to assert over"
    )
    return loaded


@pytest.fixture(scope="package")
def emitted_run(
    engine: Engine, committed_dataset: int, tmp_path_factory
) -> Iterator[EmittedRun]:
    """The tier's shared run: one `forecast-fit` at the committed shape.

    Shared because sampling is the expensive part and every assertion over stored
    rows in this tier wants the same run. Emitted at four chains of a thousand
    draws so the realized shape *is* the committed one — DV-014 compares a run's
    recorded pair against `schema_constants`, and a tiny fit would record 100
    draws and make the rule unassertable.

    **Package-scoped rather than session-scoped, deliberately.** The run is
    committed, and its `forecast_split_assignment` rows reference
    `purchase_order_line`; a session-scoped teardown would still be holding them
    while `tests/procurement` reloads that table, and the loader's delete would
    fail a foreign key. Leaving this directory is the last moment at which the
    run is still wanted and the first at which it is in another tier's way.

    Teardown removes every run that was not in `forecast_run` when this fixture
    started, so a run committed by any test in the tier is swept even if its own
    cleanup did not run.
    """
    del committed_dataset  # requested so the rows exist, not for its value
    with engine.connect() as connection:
        pre_existing = set(connection.execute(ALL_RUN_IDS_SQL).scalars())

    root = tmp_path_factory.mktemp(REPORT_ROOT_NAME)
    argv = [
        "--as-of-date",
        FIT_AS_OF_DATE.isoformat(),
        "--seed",
        str(FIT_SEED_ENTROPY),
        "--chains",
        str(FIT_CHAINS),
        "--draws",
        str(FIT_DRAWS_PER_CHAIN),
        "--tune",
        str(FIT_TUNING_DRAWS),
        "--report-root",
        str(root),
    ]
    completed = _invoke(argv)
    try:
        if completed.returncode != 0:
            pytest.fail(
                f"`{CONSOLE_SCRIPT_NAME} {' '.join(argv)}` exited {completed.returncode}; the "
                f"whole integration tier asserts over the run it emits, so there is nothing "
                f"to assert against. Standard error was:\n{completed.stderr}"
            )
        yield EmittedRun(
            run_id=uuid.UUID(completed.stdout.strip()),
            stdout=completed.stdout,
            stderr=completed.stderr,
            status=completed.returncode,
            argv=tuple(argv),
            report_root=root,
            as_of_date=FIT_AS_OF_DATE,
            seed_entropy=FIT_SEED_ENTROPY,
            chain_count=FIT_CHAINS,
            draws_per_chain=FIT_DRAWS_PER_CHAIN,
        )
    finally:
        with engine.connect() as connection:
            written = set(connection.execute(ALL_RUN_IDS_SQL).scalars()) - pre_existing
        for run_id in written:
            discard_run(engine, run_id)


@pytest.fixture
def committed_runs(engine: Engine) -> Iterator[list[uuid.UUID]]:
    """A list a test appends every run it commits to, emptied in teardown.

    The two tests in this tier that drive the job outside a rolled-back
    transaction — the provenance-warning disposition and anything else that must
    observe a real commit — register their run here rather than each writing its
    own cleanup, so "this tier leaves `forecast_run` empty" has one
    implementation.
    """
    written: list[uuid.UUID] = []
    try:
        yield written
    finally:
        for run_id in written:
            discard_run(engine, run_id)


@pytest.fixture
def fit_input(db_session: Session, emitted_run: EmittedRun) -> FitInput:
    """The shared run's pre-sampling input, rebuilt over the same rows.

    The roster and the per-vendor training counts are recomputed here rather than
    imported from `fit.py`, so a test comparing a recorded figure against them is
    comparing against a second derivation rather than against the same expression
    that produced the recorded value. Everything else — the digest, the split, the
    frame — goes through the delivered module, because those are the artifacts
    under assertion and a re-authored copy would assert nothing about them.
    """
    procurement_input = read_lines_and_events(db_session)
    row_hash = input_data_hash(procurement_input)
    split = assign_split(procurement_input.lines, emitted_run.as_of_date, row_hash)
    vendor_ids = tuple(sorted({line.vendor_id for line in procurement_input.lines}))
    material_categories = tuple(
        sorted({line.material_category for line in procurement_input.lines})
    )
    training = {
        assignment.po_line_id
        for assignment in split.assignments
        if assignment.split_side == TRAIN
    }
    counts = dict.fromkeys(vendor_ids, 0)
    for line in procurement_input.lines:
        if line.po_line_id in training:
            counts[line.vendor_id] += 1
    return FitInput(
        procurement_input=procurement_input,
        input_data_hash=row_hash,
        split=split,
        vendor_ids=vendor_ids,
        material_categories=material_categories,
        frame=training_frame(
            procurement_input.lines,
            split,
            vendor_ids,
            material_categories,
            emitted_run.as_of_date,
        ),
        training_line_counts=counts,
    )


@pytest.fixture
def stored_run(db_session: Session, emitted_run: EmittedRun) -> StoredRun:
    """The shared run read back as the manifest and rows a re-emission needs.

    Read rather than kept from the write, which is the whole point: a test that
    re-emits a variant of this run drives `write.py` against values the database
    round-tripped, so the digests, the array lengths and the JSONB shape are the
    stored ones. `draw_digest` is carried across as the **stored** bytes rather
    than recomputed, so a `double precision[]` that did not survive the round
    trip is refused by `insert_artifact_set` rather than hidden by a fresh hash.
    """
    parameters = {"run_id": emitted_run.run_id}
    row = db_session.execute(STORED_RUN_SQL, parameters).mappings().one()
    posteriors = db_session.execute(STORED_POSTERIOR_SQL, parameters).mappings().all()
    assignments = db_session.execute(STORED_ASSIGNMENT_SQL, parameters).mappings().all()

    manifest = RunManifest(
        run_id=row["run_id"],
        input_data_hash=row["input_data_hash"],
        canonical_serialization=row["canonical_serialization"],
        input_fixture_digest=row["input_fixture_digest"],
        code_commit=row["code_commit"],
        code_worktree_dirty=row["code_worktree_dirty"],
        seed_entropy=row["seed_entropy"],
        split_seed_entropy=row["split_seed_entropy"],
        library_versions=dict(row["library_versions"]),
        model_version=row["model_version"],
        artifact_schema_version=row["artifact_schema_version"],
        roster_hash=row["roster_hash"],
        chain_count=row["chain_count"],
        draw_count=row["draw_count"],
        tuning_count=row["tuning_count"],
        as_of_date=row["as_of_date"],
        horizon_days=row["horizon_days"],
        artifact_hash=bytes(row["artifact_hash"]),
        draw_serialization=row["draw_serialization"],
        wall_clock_seconds=float(row["wall_clock_seconds"]),
        input_layer=row["input_layer"],
        input_datasheet_ref=row["input_datasheet_ref"],
        covariate_names=tuple(row["covariate_names"]),
        open_line_draw_semantic=row["open_line_draw_semantic"],
        split_assignment_hash=row["split_assignment_hash"],
        held_out_fraction_declared=float(row["held_out_fraction_declared"]),
        held_out_fraction_realized=float(row["held_out_fraction_realized"]),
        held_out_uncensored_event_count=row["held_out_uncensored_event_count"],
        vendor_shrinkage={
            vendor: VendorShrinkage(
                median=float(weight["median"]),
                hpdi_low=float(weight["hpdi_low"]),
                hpdi_high=float(weight["hpdi_high"]),
            )
            for vendor, weight in row["vendor_shrinkage"].items()
        },
        open_line_count=row["open_line_count"],
        training_line_count=row["training_line_count"],
    )
    return StoredRun(
        manifest=manifest,
        line_posteriors=tuple(
            LinePosteriorRow(
                po_line_id=posterior["po_line_id"],
                draws=np.asarray(posterior["draws"], dtype=float),
                survival=np.asarray(posterior["survival"], dtype=float),
                residual_tail_mass=float(posterior["residual_tail_mass"]),
                draw_digest=bytes(posterior["draw_digest"]),
            )
            for posterior in posteriors
        ),
        assignments=tuple(
            SplitAssignment(
                po_line_id=assignment["po_line_id"],
                project_id=assignment["project_id"],
                po_number=assignment["po_number"],
                line_number=assignment["line_number"],
                split_side=assignment["split_side"],
                is_censored=assignment["is_censored"],
                canonical_ordinal=assignment["canonical_ordinal"],
            )
            for assignment in assignments
        ),
    )
