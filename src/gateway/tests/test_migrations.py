"""TR-017 / TR-050 (VR-027): the chain applies from empty and re-runs as a no-op.

TR-017 splits the obligation: the runner's forward-only and apply-from-empty
properties are **E003's to provide and this epic's to verify against**. So this
file builds a genuinely empty database and drives E003's `migrate` entry point
at it, rather than importing Alembic or reimplementing any part of the runner.

**Why an observable postcondition rather than an exit code** (TR-050). "The
runner exited zero" is satisfied by a runner that did nothing, by one that
skipped a revision, and by one that applied a revision twice with the second
application happening to be harmless. What TR-050 asks for is that *after a
second run, the ledger and the schema are identical to their state after the
first* — so the check snapshots both and compares, and the exit code is the
least of what it looks at.

**Why a throwaway database and not the developer's.** Apply-from-empty is the
property under test, and the local database is not empty — E003's chain has
already run against it. Verifying on an already-migrated database would
demonstrate only that a no-op is a no-op. Each run creates its own database and
drops it in teardown, so the check is safe to run repeatedly and against a
machine someone is working on.

**No mechanism is asserted inside E003's ledger** (TR-050, explicitly). Whether
E003 keeps per-file checksums or stock Alembic's single head revision is E003's
choice. This file reads the ledger's *contents* and compares them across runs,
which works either way.
"""

from __future__ import annotations

import ast
import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_ENTRY = REPO_ROOT / "src" / "model"

DATABASE_URL_ENV_VAR = "DATABASE_URL"

#: The revisions this epic authored, in chain order. Named rather than derived
#: from the directory, because the point is to assert *these* landed — a check
#: reading the directory would pass on a directory that had lost one.
EPIC_REVISIONS = ("0100", "0101", "0102", "0103")

#: The tables this epic owns. `data-model.md` fixes the count at three Postgres
#: tables and says "Nothing else"; the spool is SQLite and not here.
EPIC_TABLES = frozenset({"price_table_version", "price_table_entry", "llm_invocation"})

#: What "the schema" means for the comparison below. Columns catch a changed
#: type or nullability, constraints catch a changed or duplicated rule, and
#: indexes catch one created twice under different names — the three ways a
#: non-idempotent migration shows up.
SCHEMA_SNAPSHOT_SQL = """
    SELECT 'column', table_name, column_name,
           data_type || ' ' || is_nullable || ' ' || coalesce(column_default, '-')
      FROM information_schema.columns
     WHERE table_schema = 'public'
    UNION ALL
    SELECT 'constraint', conrelid::regclass::text, conname,
           pg_get_constraintdef(oid)
      FROM pg_constraint
     WHERE connamespace = 'public'::regnamespace
    UNION ALL
    SELECT 'index', tablename, indexname, indexdef
      FROM pg_indexes
     WHERE schemaname = 'public'
     ORDER BY 1, 2, 3
"""


VERSIONS_DIR = MODEL_ENTRY / "src" / "model" / "schema" / "versions"


def _revision_path(revision: str) -> Path:
    matches = sorted(VERSIONS_DIR.glob(f"{revision}_*.py"))
    assert matches, f"revision {revision} is missing from {VERSIONS_DIR}"
    assert len(matches) == 1, f"revision {revision} has {len(matches)} files: {matches}"
    return matches[0]


def _executed_statements(path: Path) -> list[str]:
    """Every SQL string the module holds, docstrings excluded.

    Parsed rather than grepped, so a docstring explaining why a statement is
    written a certain way is not mistaken for the statement — `0100`'s docstring
    discusses `ALTER TABLE ADD CONSTRAINT` at length precisely to say it avoids
    it, and the first version of this helper reported it.

    **Collected from every non-docstring string literal, not from `.execute()`
    call sites.** The narrower version scanned the source segment of each
    `execute` call, which worked while every statement was inline. `0103` now
    holds its SQL in module-level `sa.text(...)` constants and passes bound
    parameters, so the call site reads `connection.execute(_INSERT_ENTRY, {...})`
    and contains no SQL at all — the scan would have found no `INSERT` in that
    file and passed it vacuously, which is worse than failing.

    Docstrings are excluded by collecting them first and comparing by identity:
    a value-based exclusion would drop a SQL string that happened to equal one.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }

    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _configured_url() -> str:
    url = os.environ.get(DATABASE_URL_ENV_VAR, "").strip()
    if not url:
        pytest.skip(
            f"{DATABASE_URL_ENV_VAR} is not set; this tier needs a live PostgreSQL. "
            f"Start it with `docker compose up -d db` and export the variable."
        )
    return url


def _with_database(url: str, name: str) -> str:
    """Point a URL at a different database on the same server."""
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


@pytest.fixture
def empty_database() -> Iterator[str]:
    """A freshly created, entirely empty database, dropped on teardown.

    `autocommit` because PostgreSQL refuses `CREATE DATABASE` inside a
    transaction block — psycopg opens one implicitly otherwise, and the failure
    reads as a permissions problem rather than what it is.
    """
    admin_url = _with_database(_configured_url(), "postgres")
    # A fresh name per run: a leftover database from an interrupted run would
    # otherwise make the next run's "apply from empty" a lie.
    name = f"e004_migrations_{uuid.uuid4().hex[:12]}"

    try:
        admin = psycopg.connect(admin_url, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"cannot reach the database server: {exc}")

    with admin:
        admin.execute(f'CREATE DATABASE "{name}"')
        try:
            yield _with_database(_configured_url(), name)
        finally:
            # Terminate stragglers first: a connection left open by a failed
            # assertion makes DROP DATABASE hang rather than fail, and a hung
            # teardown is worse than a failed test.
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            admin.execute(f'DROP DATABASE IF EXISTS "{name}"')


def _run_migrate(url: str) -> subprocess.CompletedProcess[str]:
    """Drive E003's runner through its console entry point.

    Through `uv run --directory` rather than by importing Alembic, because
    TR-017 forbids this epic introducing migration tooling of its own and an
    in-process Alembic call would be exactly that — a second way to run the
    chain, whose agreement with the real one nothing checks.
    """
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["uv", "run", "--directory", str(MODEL_ENTRY), "migrate"],  # noqa: S607
        env={**os.environ, DATABASE_URL_ENV_VAR: url},
        capture_output=True,
        text=True,
        check=False,
    )


def _snapshot(url: str) -> tuple[list[tuple[str, ...]], list[str]]:
    """The schema and the ledger, as comparable values."""
    with psycopg.connect(url) as connection:
        schema = [tuple(row) for row in connection.execute(SCHEMA_SNAPSHOT_SQL).fetchall()]
        ledger = sorted(
            row[0] for row in connection.execute("SELECT version_num FROM alembic_version")
        )
    return schema, ledger


@pytest.fixture
def migrated_once(empty_database: str) -> str:
    result = _run_migrate(empty_database)
    assert result.returncode == 0, (
        f"the chain did not apply from empty:\n{result.stdout}\n{result.stderr}"
    )
    return empty_database


def test_the_chain_applies_from_an_empty_database(migrated_once: str) -> None:
    """TR-017's apply-from-empty, verified rather than assumed.

    The property that matters for a fresh checkout and for CI, where every run
    starts with no database at all.
    """
    _, ledger = _snapshot(migrated_once)
    assert ledger, "the chain applied but wrote no ledger entry"


def test_this_epics_tables_exist_after_the_chain(migrated_once: str) -> None:
    """Named rather than counted: "some tables were created" is true of E003's
    chain alone, so a count would pass with this epic's four revisions absent."""
    with psycopg.connect(migrated_once) as connection:
        present = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
    missing = EPIC_TABLES - present
    assert not missing, f"this epic's revisions did not create {sorted(missing)}"


def test_the_ledger_head_is_this_epics_last_revision(migrated_once: str) -> None:
    """E004's block is applied last, so its final revision is the head. If it
    were not, a later chain had been grafted on and the block claim of TR-018
    has been broken somewhere this file cannot see."""
    _, ledger = _snapshot(migrated_once)
    assert ledger == [EPIC_REVISIONS[-1]], (
        f"expected the head to be {EPIC_REVISIONS[-1]!r}, found {ledger}"
    )


def test_the_seed_landed_with_its_provenance(migrated_once: str) -> None:
    """TR-081. A seeded rate whose origin is not recorded makes every derived
    cost unattributable, so the seed is checked for provenance rather than only
    for row count."""
    with psycopg.connect(migrated_once) as connection:
        versions = connection.execute(
            "SELECT version_id, snapshot_date, source_url FROM price_table_version"
        ).fetchall()
        entry_count = connection.execute("SELECT count(*) FROM price_table_entry").fetchone()
    assert len(versions) == 1, f"expected one seeded version, found {len(versions)}"
    _, snapshot_date, source_url = versions[0]
    assert snapshot_date is not None
    assert source_url.startswith("https://"), f"source_url is not a URL: {source_url!r}"
    assert entry_count is not None and entry_count[0] >= 1, "the version has no rates"


def test_one_model_carries_two_effective_dates_in_one_version(migrated_once: str) -> None:
    """The case the primary key was shaped for, present in the seed rather than
    only described in `data-model.md`.

    Without a real instance of it, the within-version lookup of TR-039 would be
    exercised only against tables where any selection rule returns the same row
    — and "latest effective_from at or before the timestamp" would be untested
    on the only shape where it can be wrong.
    """
    with psycopg.connect(migrated_once) as connection:
        rows = connection.execute(
            "SELECT model_id, count(*) FROM price_table_entry GROUP BY model_id HAVING count(*) > 1"
        ).fetchall()
    assert rows, (
        "no seeded model carries more than one effective_from, so the within-"
        "version lookup has no case where the selection rule can be wrong"
    )


def test_a_second_run_changes_neither_the_ledger_nor_the_schema(migrated_once: str) -> None:
    """TR-050's substance, as an observable postcondition.

    Compared as values rather than trusted to an exit code. A migration that
    re-created an index under a server-generated name, or added a duplicate
    constraint, would exit zero and be caught here.
    """
    before = _snapshot(migrated_once)

    second = _run_migrate(migrated_once)
    assert second.returncode == 0, f"the second run failed:\n{second.stdout}\n{second.stderr}"

    after = _snapshot(migrated_once)
    assert after == before, (
        "the second run changed the database. Schema difference: "
        f"{sorted(set(map(str, after[0])) ^ set(map(str, before[0])))}; "
        f"ledger before {before[1]}, after {after[1]}"
    )


def test_the_second_run_applies_no_revision(migrated_once: str) -> None:
    """The mechanism behind the postcondition above, asserted separately.

    Identical snapshots would also result from a runner that re-applied every
    revision and happened to be idempotent throughout. That is a weaker
    property than forward-only, and the difference is invisible in the
    comparison — so the runner's own account of what it did is read too.
    """
    second = _run_migrate(migrated_once)
    assert second.returncode == 0
    combined = second.stdout + second.stderr
    applied = [line for line in combined.splitlines() if "Running upgrade" in line]
    assert not applied, f"the second run re-applied revisions: {applied}"


def test_each_epic_revision_file_is_re_runnable_on_its_own() -> None:
    """TR-050's second clause, which the ledger comparison cannot reach.

    The ledger makes re-running the *chain* a no-op; TR-050 additionally
    requires each file to survive being run against a database where its objects
    already exist, so a lost or reset ledger is a recoverable inconvenience
    rather than a hard failure. Read from the source, because the ledger
    guarantees the statements never run twice — there is no execution path that
    would exercise this.

    `ALTER TABLE ... ADD CONSTRAINT` is the specific trap: PostgreSQL 16 gives
    it no `IF NOT EXISTS` form, so a constraint added that way raises on a
    re-run. Declaring constraints inline in `CREATE TABLE IF NOT EXISTS` avoids
    it, which is why the revisions are written that way.
    """
    offenders: list[str] = []
    for revision in EPIC_REVISIONS:
        path = _revision_path(revision)
        for statement in _executed_statements(path):
            upper = statement.upper()
            for keyword, guard in (
                ("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"),
                ("CREATE INDEX", "CREATE INDEX IF NOT EXISTS"),
                ("INSERT INTO", "ON CONFLICT"),
            ):
                if keyword in upper and guard not in upper:
                    offenders.append(f"{path.name}: {keyword} without {guard}")
            if "ALTER TABLE" in upper and "ADD CONSTRAINT" in upper:
                offenders.append(
                    f"{path.name}: ALTER TABLE ... ADD CONSTRAINT has no "
                    f"IF NOT EXISTS form and would raise on a re-run"
                )
    assert not offenders, "revisions are not re-runnable on their own: " + "; ".join(offenders)


def test_the_re_runnable_scan_reads_statements_and_not_prose() -> None:
    """A test of the check above, and it earned its place by catching it.

    The first version read the whole file and reported `0100` — whose docstring
    *explains* why it avoids `ALTER TABLE ADD CONSTRAINT*. A scan that cannot
    tell an executed statement from a sentence about one will fire on any
    revision whose author documented their reasoning, which trains the next
    reader to ignore it.
    """
    statements = _executed_statements(_revision_path("0100"))
    assert statements, "no executed statements found in 0100"
    joined = " ".join(statements)
    assert "CREATE TABLE IF NOT EXISTS price_table_version" in joined
    assert "docstring" not in joined.lower(), "the extractor is returning prose, not statements"
    # 0100's module docstring is the one that discusses the forbidden statement.
    assert "ALTER TABLE ADD CONSTRAINT" not in joined, (
        "the extractor picked up the module docstring, which explains why this "
        "revision avoids ALTER TABLE ADD CONSTRAINT -- reporting it would fire "
        "on any revision whose author documented their reasoning"
    )


def test_the_re_runnable_scan_sees_sql_held_in_a_module_constant() -> None:
    """The second hole the scan had, and the one that would have passed silently.

    `0103` holds its `INSERT` in a module-level `sa.text(...)` constant and
    passes bound parameters, so its `execute` call site carries no SQL. A scan
    reading call sites would find no `INSERT INTO` in that file and conclude it
    needs no `ON CONFLICT` -- reporting the seed re-runnable without ever having
    looked at it.
    """
    joined = " ".join(_executed_statements(_revision_path("0103")))
    assert "INSERT INTO price_table_entry" in joined, (
        "the extractor missed SQL held in a module constant"
    )
    assert "ON CONFLICT" in joined


@pytest.mark.parametrize("revision", EPIC_REVISIONS)
def test_every_epic_revision_refuses_to_downgrade(revision: str) -> None:
    """Forward-only is E003's to provide; this epic's revisions must not break it.

    **Asserted from the source rather than by importing and calling, and the
    reason is architectural.** A revision module's first statement is
    `from alembic import op`, and the gateway entry does not resolve alembic —
    `tests/checks/test_dependency_isolation.py` asserts it must not, because the
    migration stack belongs to the schema owner alone ({SAD:ADR-0016}). An
    import here would either fail, as it did when this test was first written
    that way, or be "fixed" by adding alembic to the gateway's manifest, which
    would break a QC-passed check of another epic to make a test of this one
    more direct.

    So this parses for the behaviour instead. Weaker than calling it, and the
    weakness is bounded: it confirms `downgrade` exists and that its body raises
    `NotImplementedError`, which is the whole of what these four stubs do.
    Anything more elaborate would need the runner's own environment, and that is
    E003's suite, not this one.
    """
    tree = ast.parse(_revision_path(revision).read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "downgrade" in functions, (
        f"{revision} defines no `downgrade`; Alembic calls that attribute when a "
        f"downgrade is requested, and a missing one fails with an unexplained "
        f"AttributeError instead of stating the policy"
    )
    raises = [
        node
        for node in ast.walk(functions["downgrade"])
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == "NotImplementedError"
    ]
    assert raises, f"{revision}'s downgrade does not raise NotImplementedError"
