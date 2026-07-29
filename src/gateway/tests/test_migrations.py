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
import shutil
import subprocess
import sys
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

#: The inclusive filename-prefix block {SAD:ADR-0013} assigns to E004, as
#: numbers. Held separately from the tuple above so the two can disagree: a
#: revision renumbered out of the block leaves `EPIC_REVISIONS` naming a file
#: that is gone, and a foreign revision numbered *into* the block appears in the
#: block scan without being named. Both are failures below, and neither is
#: visible from the tuple alone.
EPIC_BLOCK = (100, 199)

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


def _declared(path: Path, name: str) -> str | None:
    """A revision module's module-level `revision` / `down_revision` literal.

    Read from the source for the same reason `test_every_epic_revision_refuses_
    to_downgrade` parses rather than imports: a revision module opens with
    `from alembic import op`, and the gateway entry deliberately does not resolve
    alembic ({SAD:ADR-0016}).

    Both spellings are accepted because the annotation is a convention rather
    than a guarantee — a revision written without one would otherwise be read as
    declaring nothing at all, which is a silent pass.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target: str | None = node.target.id
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target = node.targets[0].id
        else:
            continue
        if target != name or node.value is None:
            continue
        assert isinstance(node.value, ast.Constant), (
            f"{path.name} declares `{name}` as something other than a literal; "
            f"this reader cannot follow it and would otherwise skip the revision"
        )
        value = node.value.value
        assert value is None or isinstance(value, str), (
            f"{path.name} declares `{name}` as {value!r}, which is neither a revision id nor None"
        )
        return value
    raise AssertionError(f"{path.name} declares no module-level `{name}`")


def _ancestry(head: str) -> list[str]:
    """The chain the ledger's head stands on, oldest first.

    Walked back through `down_revision` from the applied head rather than read
    off the directory: the directory holds every revision *file*, and what the
    assertions below need is the subset a migrated database actually ran.
    """
    chain: list[str] = []
    seen: set[str] = set()
    current: str | None = head
    while current is not None:
        assert current not in seen, (
            f"the revision graph loops at {current!r}; walked {chain} from head {head!r}"
        )
        seen.add(current)
        chain.append(current)
        current = _declared(_revision_path(current), "down_revision")
    chain.reverse()
    return chain


def _revision_files() -> list[Path]:
    """Every revision module in the directory, whichever epic authored it."""
    return sorted(path for path in VERSIONS_DIR.glob("*.py") if path.name != "__init__.py")


def _declared_link(path: Path) -> tuple[str, str | None]:
    """A revision module's own `(revision, down_revision)`.

    Read from the module rather than from its filename: the two can disagree
    and the runner obeys the contents. Parsed rather than imported, for the
    architectural reason recorded on the downgrade test below — the gateway
    entry does not resolve `alembic`, and a revision module's first statement
    imports it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    declared: dict[str, str | None] = {}
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign | ast.Assign) or node.value is None:
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        names = {target.id for target in targets if isinstance(target, ast.Name)}
        for name in names & {"revision", "down_revision"}:
            assert isinstance(node.value, ast.Constant), (
                f"{path.name}: `{name}` is not a literal, so the chain cannot be "
                f"read without importing the module"
            )
            value = node.value.value
            assert value is None or isinstance(value, str), (
                f"{path.name}: `{name}` is {value!r}; this file reads single-parent "
                f"chains only, and a branch point needs a decision, not a coercion"
            )
            declared[name] = value

    revision = declared.get("revision")
    assert isinstance(revision, str), f"{path.name} declares no `revision`"
    return revision, declared.get("down_revision")


def _chain_on_disk() -> list[str]:
    """The whole directory's chain in `down_revision` order, root first.

    **Derived, never named.** Ordering is `down_revision` and only
    `down_revision`; the numeric prefix is a block claim (TR-018) and is never
    compared to decide what runs. Walking the links is therefore the only way
    to know which revision is last, and it is the one property here that
    legitimately moves — a later epic extending the chain is the arrangement
    working, not a defect.
    """
    predecessor = dict(_declared_link(path) for path in _revision_files())
    assert predecessor, f"no revision modules found in {VERSIONS_DIR}"

    roots = sorted(revision for revision, down in predecessor.items() if down is None)
    assert len(roots) == 1, f"expected exactly one root revision, found {roots}"

    successors: dict[str, list[str]] = {}
    for revision, down in predecessor.items():
        if down is not None:
            successors.setdefault(down, []).append(revision)

    chain: list[str] = []
    current: str | None = roots[0]
    while current is not None:
        chain.append(current)
        following = sorted(successors.get(current, []))
        assert len(following) <= 1, f"{current} is followed by {following}; the chain branches"
        current = following[0] if following else None

    assert len(chain) == len(predecessor), (
        f"the chain reaches {len(chain)} of {len(predecessor)} revisions on disk; "
        f"unreachable: {sorted(set(predecessor) - set(chain))}"
    )
    return chain


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


def test_this_epics_block_is_contiguous_and_in_order() -> None:
    """TR-018's block claim over the chain **on disk**, without a database.

    **This assertion used to be "the head is `0103`", and that was a stronger
    claim than TR-018 makes.** A block is contiguous and ordered; it is not
    permanently last. E007 chained `0300`-`0303` off `0103` — the documented
    way a later epic extends one shared chain — and the literal turned a
    correct extension into a red test, which trains the next author to bump the
    literal rather than read what broke.

    What the original docstring was reaching for survives here in full: if
    another epic's revision had been *grafted into* the middle of `0100`-`0103`,
    or one of the four had gone missing or been reordered, this epic's block
    would no longer be one unbroken run and the claim would be broken somewhere
    this file cannot see. That is what is asserted, over the chain rather than
    over the filenames.

    **Kept alongside the applied-chain form below, not folded into it**
    (reconciled 2026-07-28, when E006 and E007 arrived at the same file having
    each replaced the head literal). This one needs no database and therefore
    runs everywhere; that one needs a migrated database and is skipped without
    one, but sees what the runner *did* rather than what the directory says it
    would do. A directory that is correct and a chain that applied are different
    claims, and dropping either leaves one of them unasserted in some
    environment the build runs in.
    """
    chain = _chain_on_disk()
    missing = [revision for revision in EPIC_REVISIONS if revision not in chain]
    assert not missing, f"this epic's revisions are not in the chain: {missing}"

    positions = [chain.index(revision) for revision in EPIC_REVISIONS]
    span = chain[positions[0] : positions[0] + len(EPIC_REVISIONS)]
    assert span == list(EPIC_REVISIONS), (
        f"this epic's block is not one unbroken run: expected {list(EPIC_REVISIONS)} "
        f"consecutively, the chain has {span} at that position. Whole chain: {chain}"
    )


def test_this_epics_block_is_applied_whole_and_contiguous(migrated_once: str) -> None:
    """TR-018's block claim, asserted as a property of E004's four revisions
    rather than of whatever happens to be last in the directory.

    **What this used to assert, and why that was wrong.** It read the ledger and
    demanded the head equal `0103` — E004's final revision — on the reasoning
    that "E004's block is applied last, so its final revision is the head". That
    reasoning held only while E004 was the newest epic. It is not what TR-018
    says: TR-018 confines E004's revisions to `0100`-`0199`, and says nothing
    about E004 being the end of the chain. Later epics chained on top of `0103`,
    exactly as the block scheme intends, and the assertion failed the build for a
    chain that is correct. A check that fails when the design works is not
    enforcing the design.

    **What it asserts instead**, all four against the chain a migrated database
    actually ran:

    1. one head, so there is one chain to talk about;
    2. every one of E004's four revisions is in it — a lost or renumbered
       revision is missing here;
    3. they are consecutive, so no later epic has been interleaved into the
       middle of E004's block;
    4. the revisions the chain carries inside `0100`-`0199` are *exactly* those
       four, in order — which is the block claim itself, and what fails if one is
       renumbered out of the block or a foreign revision is numbered into it.

    E004 no longer being last is now the expected case rather than a failure, and
    the chain descending from `0103` is asserted by (2): the walk starts at the
    applied head, so a revision present in it is an ancestor of that head.

    The four controls below plant damage in a copy of the revision directory and
    require *this* function to report it; they are what stop a broad, quiet
    check from rotting into one that passes on anything.
    """
    _, ledger = _snapshot(migrated_once)
    assert len(ledger) == 1, (
        f"the ledger records {ledger}; a chain with more than one head means "
        f"`migrate` applied one of several branches and everything below "
        f"describes whichever it picked"
    )
    chain = _ancestry(ledger[0])

    missing = [revision for revision in EPIC_REVISIONS if revision not in chain]
    assert not missing, (
        f"this epic's revisions {missing} are not in the applied chain. Head "
        f"{ledger[0]!r} stands on {chain}"
    )

    positions = [chain.index(revision) for revision in EPIC_REVISIONS]
    assert positions == list(range(positions[0], positions[0] + len(EPIC_REVISIONS))), (
        f"this epic's revisions are not contiguous in the applied chain: they sit "
        f"at {positions} of {chain}. Something has been chained into the middle of "
        f"the block TR-018 reserves"
    )

    low, high = EPIC_BLOCK
    in_block = [revision for revision in chain if low <= int(revision) <= high]
    assert in_block == list(EPIC_REVISIONS), (
        f"the applied chain carries {in_block} inside the {low:04d}-{high:04d} block "
        f"{{SAD:ADR-0013}} reserves for E004, and this epic authored "
        f"{list(EPIC_REVISIONS)}. Either one of ours was renumbered out of the block "
        f"or another epic numbered a revision into it"
    )


# --- the negative control for the assertion above ---------------------------
#
# The old head assertion could fail, loudly and wrongly, so nobody had to ask
# whether it could fail at all. The restatement is broader and quieter, and a
# broad quiet check is exactly the kind that rots into one that passes on
# anything. These three plant the damage TR-018 exists to catch and require the
# assertion to report it.
#
# Damage is planted in a *copy* of the revision directory under pytest's own
# basetemp, never in the real one: the real directory is the modelling entry's
# and a check of this epic has no business editing it, even transiently.

#: `NNNN_name.py` — E003's convention, and what makes a copy filterable.
REVISION_GLOB = "[0-9][0-9][0-9][0-9]_*.py"


def _copy_of_the_revision_directory(tmp_path: Path) -> Path:
    target = tmp_path / "versions"
    target.mkdir()
    for path in VERSIONS_DIR.glob(REVISION_GLOB):
        shutil.copy2(path, target / path.name)
    assert len(list(target.glob(REVISION_GLOB))) >= len(EPIC_REVISIONS), (
        f"the copy at {target} holds fewer revisions than this epic authored"
    )
    return target


def _only(directory: Path, revision: str) -> Path:
    matches = sorted(directory.glob(f"{revision}_*.py"))
    assert len(matches) == 1, f"{revision} has {len(matches)} files in {directory}"
    return matches[0]


def _repoint(directory: Path, revision: str, new_parent: str) -> None:
    path = _only(directory, revision)
    text = path.read_text(encoding="utf-8")
    old = f'down_revision: str | Sequence[str] | None = "{_declared(path, "down_revision")}"'
    assert old in text, f"{path.name} does not spell its parent the way this helper expects"
    path.write_text(
        text.replace(old, f'down_revision: str | Sequence[str] | None = "{new_parent}"'),
        encoding="utf-8",
    )


def _renumber(directory: Path, revision: str, new_revision: str) -> None:
    path = _only(directory, revision)
    text = path.read_text(encoding="utf-8")
    old = f'revision: str = "{revision}"'
    assert old in text, f"{path.name} does not spell its id the way this helper expects"
    renamed = directory / path.name.replace(f"{revision}_", f"{new_revision}_", 1)
    renamed.write_text(text.replace(old, f'revision: str = "{new_revision}"'), encoding="utf-8")
    path.unlink()


def _head_of(directory: Path) -> str:
    revisions = {
        _declared(path, "revision"): _declared(path, "down_revision")
        for path in directory.glob(REVISION_GLOB)
    }
    pointed_at = {parent for parent in revisions.values() if parent is not None}
    heads = sorted(revision for revision in revisions if revision not in pointed_at)
    assert len(heads) == 1, f"the doctored directory has {len(heads)} heads: {heads}"
    head = heads[0]
    assert head is not None
    return head


def _damage(directory: Path, kind: str) -> None:
    if kind == "renumbered_out_of_the_block":
        # `0102` moved into E005's block. The four files still exist and the
        # chain still resolves — only the block claim is broken.
        _renumber(directory, "0102", "0202")
        _repoint(directory, "0103", "0202")
    elif kind == "one_revision_dropped":
        _repoint(directory, "0103", "0101")
        _only(directory, "0102").unlink()
    elif kind == "a_foreign_revision_inside_the_block":
        # Another epic numbering into `0100`-`0199`, chained through the middle
        # of E004's four. Breaks contiguity and the block scan at once.
        (directory / "0150_foreign.py").write_text(
            'revision: str = "0150"\ndown_revision: str | Sequence[str] | None = "0101"\n',
            encoding="utf-8",
        )
        _repoint(directory, "0102", "0150")
    else:  # pragma: no cover - the parametrisation below is closed
        raise AssertionError(f"unknown damage {kind!r}")


@pytest.mark.parametrize(
    "kind",
    ["renumbered_out_of_the_block", "one_revision_dropped", "a_foreign_revision_inside_the_block"],
)
def test_the_block_assertion_reports_a_damaged_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    """Each of the three ways TR-018's block claim breaks, planted and caught.

    The real assertion function is called rather than a re-implementation of it,
    with two seams redirected: `VERSIONS_DIR` at the doctored copy, and
    `_snapshot` at the head that copy resolves to. Nothing else is stubbed, so
    what runs is the same walk and the same four assertions.
    """
    directory = _copy_of_the_revision_directory(tmp_path)
    _damage(directory, kind)

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "VERSIONS_DIR", directory)
    head = _head_of(directory)
    monkeypatch.setattr(module, "_snapshot", lambda url: ([], [head]))

    with pytest.raises(AssertionError):
        test_this_epics_block_is_applied_whole_and_contiguous("not a database url")


def test_the_block_assertion_passes_over_an_undamaged_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The positive control the three above need to mean anything.

    Without it, a copy helper that produced an unreadable directory would make
    every damage case "fail correctly" for the wrong reason, and the negative
    controls would be evidence about the copier.
    """
    directory = _copy_of_the_revision_directory(tmp_path)

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "VERSIONS_DIR", directory)
    head = _head_of(directory)
    monkeypatch.setattr(module, "_snapshot", lambda url: ([], [head]))

    test_this_epics_block_is_applied_whole_and_contiguous("not a database url")


def test_the_ledger_head_is_the_last_revision_on_disk(migrated_once: str) -> None:
    """Whatever is last in the chain is what a run from empty must land on.

    Read from the directory rather than named, so an epic extending the chain
    moves the expectation with it — while a revision that exists on disk and
    never applied, or a runner stopping short of the end, still fails. The
    single-element comparison is the point: two rows in `alembic_version` mean
    two heads, which is a branch nobody chose.
    """
    _, ledger = _snapshot(migrated_once)
    head = _chain_on_disk()[-1]
    assert ledger == [head], f"expected the head to be {head!r}, found {ledger}"


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
