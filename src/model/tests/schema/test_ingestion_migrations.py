"""E006's migration set: FR-040, VR-013, VR-014, and the object inventory.

Five properties of the `0400`-`0499` block as a *set*, none of which any one
revision can state about itself:

* **The block is respected and the chain is named for itself.** Every revision
  E006 authors carries an `04xx` prefix, its `revision` string equals that
  prefix, and each one chains from the revision the migration sequence declares
  -- `0400` from E007's head `0303`, then `0401`-`0404` in order. Ordering is
  `down_revision` and only `down_revision`; the prefixes are a *claim on a number
  range* (`tests/checks/test_migration_ranges.py`), and a file whose name
  disagrees with its contents defeats the claim's whole purpose.
* **One head, and it is E006's last revision.** Two heads is what parallel
  authoring produces, and `alembic upgrade head` then refuses to choose.
* **Applies from empty.** A chain that works on *this* database can still fail on
  a fresh one, because this database has accumulated objects the migrations never
  created. Only a genuinely empty database tests the claim (VR-013).
* **Re-application at head is a no-op.** Not "exits zero" -- `alembic upgrade
  head` exits zero whether it applied five migrations or none. The assertion is
  on the list of migration bodies that actually executed, which must be empty
  (VR-014).
* **The delivered object set is exactly the declared inventory.** `data-model.md`
  §Named Object Inventory is the contract: a constraint whose name is not written
  down cannot be referenced by a later migration's `DROP CONSTRAINT`, cannot be
  *expected* by another epic's test, and forces any test that wants to assert on
  it to match message text instead -- which is locale- and version-dependent.
  Asserted in both directions, because each catches a different defect: a missing
  object is an invariant that silently is not enforced, and an *extra* one is an
  object nobody reviewed.

Downgrade bodies are not asserted here and their absence is deliberate rather
than an omission: `test_migration_chain.py` parametrizes both of its
downgrade tests over every revision the chain discovers, so `0400`-`0404` are
covered there the moment they land -- by calling `downgrade()` and requiring
`NotImplementedError`, and by an AST scan for `op.*` calls in the body. A second
copy here would be a second answer about the same files.

**Why the object inventory is a literal list in this file.** Everywhere else in
this suite the expected set is derived from the catalog or parsed from the
artifact. Not here: the inventory *is* the contract, so restating it as a set of
names a human wrote is the assertion. Deriving it from the migrations would
compare the migrations with themselves, and parsing it out of the artifact's
markdown table would turn a documentation reformat into a schema failure.
`test_table_ownership.py` already asserts the other half -- that every one of
these names appears in a data model -- from the catalog, so the two together
close the loop without either doing the other's job.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from model.schema.cli import DEFAULT_REVISION, EXIT_OK, build_config, main

#: `src/model/tests/schema/` -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[4]
VERSIONS_DIR = REPO_ROOT / "src" / "model" / "src" / "model" / "schema" / "versions"

#: E006's reserved filename-prefix block, inclusive (FR-040). Declared in
#: `tests/checks/test_migration_ranges.py`, which asserts the partition across
#: every epic; this module asserts only that E006 stayed inside its own claim.
E006_BLOCK = (400, 499)

#: The migration sequence `data-model.md` fixes, as `(revision, down_revision)`
#: in application order. `0400` chains from `0303`, E007's head -- it was
#: authored on `0103` as `0300` and re-parented on 2026-07-28, when E007's
#: concurrent claim on `0300`-`0399` landed first and this epic renumbered.
#:
#: Written out rather than derived: the chain each later revision needs is
#: a design fact -- every one of them carries a composite foreign key to a key an
#: earlier one creates -- and a test that read the order out of the files would
#: agree with any order they happened to declare.
E006_SEQUENCE: tuple[tuple[str, str], ...] = (
    ("0400", "0303"),
    ("0401", "0400"),
    ("0402", "0401"),
    ("0403", "0402"),
    ("0404", "0403"),
)

E006_REVISIONS: tuple[str, ...] = tuple(revision for revision, _ in E006_SEQUENCE)
E006_HEAD = E006_REVISIONS[-1]

#: `NNNN_name.py`.
REVISION_FILENAME = re.compile(r"^(?P<prefix>\d{4})_[a-z0-9_]+\.py$")

# --------------------------------------------------------------------------- #
# data-model.md §Named Object Inventory -- the contract, restated
# --------------------------------------------------------------------------- #

E006_TABLES: tuple[str, ...] = (
    "ingestion_run",
    "ingestion_run_document",
    "ingestion_run_chunk",
    "ingestion_run_extracted_value",
    "ingestion_run_extraction_failure",
    "extracted_value_line_item",
    "extracted_value_parse_signal",
)

E006_VIEW = "v_active_ingestion_generation"

#: The view's columns, in the declared order. Included because the view is where
#: run attribution is obtained at all: a consumer reads the chunker version and
#: embedding revision from here, and a column quietly dropped from the projection
#: would leave every reader of it joining back to `ingestion_run` by hand.
E006_VIEW_COLUMNS: tuple[str, ...] = (
    "document_id",
    "run_id",
    "input_tuple_digest",
    "committed_at",
    "agent_id",
    "provider_model",
    "chunker_version",
    "embedding_model_id",
    "embedding_model_revision",
    "resolution_mode",
    "confidence_floor",
    "started_at",
    "finished_at",
)

#: Every index the inventory names, including the ones a primary key or unique
#: constraint creates implicitly. Indexes are in scope and are not an
#: afterthought: `ix_ingestion_run_document__single_active` is the *entire*
#: mechanism behind "one live generation per document", and an index nobody
#: documented is an invariant nobody knows is load-bearing.
E006_INDEXES: frozenset[str] = frozenset(
    {
        "pk_ingestion_run",
        "ix_ingestion_run__started_at",
        "pk_ingestion_run_document",
        "ix_ingestion_run_document__single_active",
        "ix_ingestion_run_document__document",
        "pk_ingestion_run_chunk",
        "ix_ingestion_run_chunk__generation",
        "pk_ingestion_run_extracted_value",
        "uq_ingestion_run_extracted_value__value_generation",
        "ix_ingestion_run_extracted_value__generation",
        "pk_ingestion_run_extraction_failure",
        "ix_ingestion_run_extraction_failure__generation",
        "pk_extracted_value_line_item",
        "ix_extracted_value_line_item__item",
        "pk_extracted_value_parse_signal",
        "ix_extracted_value_parse_signal__generation",
    }
)

#: Every constraint the inventory names, mapped to its `pg_constraint.contype`.
#: The kind is carried because the name alone would let a `CHECK` be replaced by
#: a foreign key of the same name, which is a different rule wearing a reviewed
#: name.
E006_CONSTRAINTS: dict[str, str] = {
    # ingestion_run -- 0400
    "pk_ingestion_run": "p",
    "ck_ingestion_run__agent_id_present": "c",
    "ck_ingestion_run__agent_id_format": "c",
    "ck_ingestion_run__provider_model_present": "c",
    "ck_ingestion_run__chunker_version_present": "c",
    "ck_ingestion_run__embedding_model_id_present": "c",
    "ck_ingestion_run__embedding_model_revision_present": "c",
    "ck_ingestion_run__corpus_manifest_digests": "c",
    "ck_ingestion_run__extraction_prompt_digest_format": "c",
    "ck_ingestion_run__extraction_schema_digest_format": "c",
    "ck_ingestion_run__resolution_mode": "c",
    "ck_ingestion_run__run_trace_id_format": "c",
    "ck_ingestion_run__run_trace_id_not_all_zero": "c",
    "ck_ingestion_run__confidence_floor_range": "c",
    "ck_ingestion_run__deduction_alternate_label_range": "c",
    "ck_ingestion_run__deduction_page_split_range": "c",
    "ck_ingestion_run__deduction_repaired_range": "c",
    "ck_ingestion_run__floor_excludes_repair": "c",
    "ck_ingestion_run__floor_excludes_alt_split": "c",
    "ck_ingestion_run__finished_after_started": "c",
    "ck_ingestion_run__failure_kind_domain": "c",
    "ck_ingestion_run__failure_detail_iff_kind": "c",
    "ck_ingestion_run__failed_run_unfinished": "c",
    # ingestion_run_document -- 0401
    "pk_ingestion_run_document": "p",
    "ck_ingestion_run_document__status": "c",
    "ck_ingestion_run_document__tuple_digest_format": "c",
    "fk_ingestion_run_document__run": "f",
    "fk_ingestion_run_document__document": "f",
    # the three run-output associations -- 0402
    "pk_ingestion_run_chunk": "p",
    "fk_ingestion_run_chunk__chunk": "f",
    "fk_ingestion_run_chunk__generation": "f",
    "pk_ingestion_run_extracted_value": "p",
    "uq_ingestion_run_extracted_value__value_generation": "u",
    "fk_ingestion_run_extracted_value__value": "f",
    "fk_ingestion_run_extracted_value__generation": "f",
    "pk_ingestion_run_extraction_failure": "p",
    "fk_ingestion_run_extraction_failure__failure": "f",
    "fk_ingestion_run_extraction_failure__generation": "f",
    # the two value-level associations -- 0403
    "pk_extracted_value_line_item": "p",
    "ck_extracted_value_line_item__ordinal_non_negative": "c",
    "fk_extracted_value_line_item__run_output": "f",
    "pk_extracted_value_parse_signal": "p",
    "ck_extracted_value_parse_signal__label_match": "c",
    "ck_extracted_value_parse_signal__source_count_positive": "c",
    "fk_extracted_value_parse_signal__run_output": "f",
    "fk_extracted_value_parse_signal__value_count": "f",
}

#: PostgreSQL's identifier limit. Names are truncated silently past it, and two
#: truncated names can collide -- at which point a test matching the untruncated
#: name never matches and the constraint it names is never exercised.
IDENTIFIER_BYTE_LIMIT = 63

# --------------------------------------------------------------------------- #
# Catalog queries. Module-level constants, never assembled from values (S608).
# --------------------------------------------------------------------------- #

RELATION_KIND_SQL = """
SELECT cls.relkind
FROM pg_class cls
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
WHERE nsp.nspname = 'public' AND cls.relname = :relation_name
"""

VIEW_COLUMNS_SQL = """
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = :view_name
ORDER BY ordinal_position
"""

#: Every index on an E006 table, whatever created it.
E006_INDEXES_SQL = """
SELECT indexname
FROM pg_indexes
WHERE schemaname = 'public' AND tablename = ANY(:tables)
ORDER BY indexname
"""

#: Every constraint *on* an E006 table. Constraints are stored on the table they
#: constrain, so a foreign key from an E006 table to an E003 one appears here --
#: correctly, since E006 authored it -- while one pointing the other way would
#: not, and is TR-036's business rather than this test's.
#:
#: Restricted to the four constraint kinds this schema declares. PostgreSQL 17
#: records `NOT NULL` in `pg_constraint` as contype `n`; PostgreSQL 16 does not,
#: and pinning the kinds keeps a server upgrade from reporting every NOT NULL
#: column as an undeclared object.
E006_CONSTRAINTS_SQL = """
SELECT con.conname, con.contype
FROM pg_constraint con
JOIN pg_class cls ON cls.oid = con.conrelid
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
WHERE nsp.nspname = 'public'
  AND cls.relname = ANY(:tables)
  AND con.contype IN ('p', 'u', 'f', 'c')
ORDER BY con.conname
"""

STAMPED_REVISION_SQL = "SELECT version_num FROM alembic_version"


def _revision_files() -> list[Path]:
    assert VERSIONS_DIR.is_dir(), (
        f"no revision directory at {VERSIONS_DIR}. Every assertion in this module reads it, "
        f"and an absent directory would make the file-level ones pass over nothing."
    )
    return sorted(
        path
        for path in VERSIONS_DIR.glob("*.py")
        if path.name != "__init__.py" and REVISION_FILENAME.match(path.name)
    )


def _prefix(path: Path) -> int:
    match = REVISION_FILENAME.match(path.name)
    assert match is not None, f"unreachable: {path.name} passed the filter"
    return int(match.group("prefix"))


def _e006_revision_files() -> list[Path]:
    low, high = E006_BLOCK
    return [path for path in _revision_files() if low <= _prefix(path) <= high]


def _scripts_by_revision() -> dict[str, Any]:
    """Every revision Alembic resolves, keyed by id.

    Resolved through `build_config()` -- the same function the `migrate` console
    entry point uses -- so this module cannot disagree with the runner about
    which directory holds the chain.
    """
    return {
        script.revision: script
        for script in ScriptDirectory.from_config(build_config()).walk_revisions()
    }


@contextmanager
def _recording_applied_revisions() -> Iterator[list[str]]:
    """Record the revision id of every migration body that actually executes.

    Yields a list that fills as the chain runs.
    `MigrationContext.run_migrations` calls each registered `on_version_apply`
    callback once per step, *after* that step's `upgrade()` body has returned, so
    an entry here means the body ran -- not that Alembic considered it, and not
    that the command exited zero. That distinction is the whole content of the
    re-application claim.

    `EnvironmentContext.configure` is wrapped rather than `env.py` being taught a
    test hook: `env.py` is production code, and a branch in it that exists only
    for tests is a branch that can drift from the path CI exercises. The wrapper
    appends to any `on_version_apply` the environment may itself set and is
    removed unconditionally on the way out, so no later test inherits it.

    A near-copy of `test_migration_chain.py`'s helper of the same name, and
    deliberately not an import from it. Importing a private helper out of a
    sibling test module depends on pytest's `sys.path` insertion, which
    `conftest.py` records as holding only until someone adds an `__init__.py`
    here -- and a shared copy in `conftest.py` would put a monkeypatch of an
    Alembic class in the fixture file every schema test loads.
    """
    applied: list[str] = []

    def record(*, step: Any, **_unused: Any) -> None:
        # `step` is Alembic's MigrationInfo. Stamps move the version pointer
        # without running a body; only genuine migrations count as work.
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


def _migrate(revision: str = DEFAULT_REVISION) -> list[str]:
    """Run the `migrate` console entry point; return the revisions that executed.

    The entry point rather than `alembic.command.upgrade`, because the sequence
    as anyone actually applies it is `uv run --directory src/model migrate` --
    exit-code contract included.
    """
    with _recording_applied_revisions() as applied:
        exit_code = main([revision])

    assert exit_code == EXIT_OK, (
        f"`migrate {revision}` exited {exit_code}, not {EXIT_OK}. It applied {applied} first; "
        f"anything asserted below about that list would be measuring a failed run."
    )
    return applied


def _stamped_revisions(url: URL) -> set[str]:
    """The contents of `alembic_version` in the database at `url`.

    A short-lived engine disposed immediately: the scratch fixture is about to
    drop this database, and a pooled connection left open would make `DROP
    DATABASE` fail for a reason unrelated to the test.
    """
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return set(connection.execute(text(STAMPED_REVISION_SQL)).scalars())
    finally:
        engine.dispose()


def _relation_kinds(url: URL, relation_name: str) -> list[str]:
    """`pg_class.relkind` for `relation_name` at `url`, empty when it is absent."""
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return [
                kind
                for (kind,) in connection.execute(
                    text(RELATION_KIND_SQL), {"relation_name": relation_name}
                )
            ]
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- #
# FR-040 -- the block, the names, and the declared chain
# --------------------------------------------------------------------------- #


def test_the_revision_files_in_e006_s_block_are_exactly_the_declared_set() -> None:
    """FR-040, VR-013: `04xx` holds these five revisions and no others.

    Both directions. A sixth `04xx` file is a revision authored outside the
    migration sequence `data-model.md` fixes -- which is where the composite
    foreign keys' ordering is decided -- and a missing one is an object the rest
    of the epic is written against.
    """
    found = sorted(path.name for path in _e006_revision_files())
    prefixes = sorted(f"{_prefix(path):04d}" for path in _e006_revision_files())

    assert prefixes == sorted(E006_REVISIONS), (
        f"E006's block holds {found}, whose prefixes are {prefixes}; the declared sequence is "
        f"{sorted(E006_REVISIONS)}. `data-model.md` §Migration Sequence is normative: a "
        f"revision that is not in it has no declared position among the composite foreign "
        f"keys that make each one depend on the last."
    )


@pytest.mark.parametrize(("revision", "down_revision"), E006_SEQUENCE, ids=E006_REVISIONS)
def test_each_e006_revision_declares_its_prefix_and_its_declared_parent(
    revision: str, down_revision: str
) -> None:
    """FR-040: id equals filename prefix, and the parent is the declared one.

    The revision id *is* the filename prefix in this project (`alembic.ini`'s
    `file_template` is `%(rev)s_%(slug)s`), so a file whose name disagrees with
    its contents makes the block partition -- which reads names -- describe a
    directory the runner does not run.

    The parent is asserted because ordering is `down_revision` and *only*
    `down_revision`. `0400` chaining from `0303` rather than from `0010` is what
    puts E006's block after everything already in the chain rather than beside
    it -- E004's `0103` while this epic was authored, E007's `0303` since the
    two epics' concurrent claims on `0300`-`0399` were untangled -- and
    `0401`-`0404`
    are a hard order: each carries a composite foreign key to a key the previous
    one creates.
    """
    scripts = _scripts_by_revision()

    assert revision in scripts, (
        f"revision {revision!r} is not in the chain Alembic resolves ({sorted(scripts)}). A "
        f"file that exists but is unreachable from the base never runs."
    )

    script = scripts[revision]
    filename = Path(script.path).name

    assert filename.startswith(f"{revision}_"), (
        f"{filename!r} holds revision {revision!r} but is not named for it. Rename it to "
        f"{revision}_<slug>.py so a directory listing reads as the chain."
    )
    assert script.down_revision == down_revision, (
        f"revision {revision!r} chains from {script.down_revision!r}; `data-model.md` "
        f"§Migration Sequence declares {down_revision!r}. Ordering is down_revision and only "
        f"down_revision, so this is the statement that decides what runs before what."
    )


def test_the_chain_resolves_to_one_head_and_it_is_e006_s_last_revision() -> None:
    """FR-040, VR-013: a single head, and E006 left it at `0404`.

    Two heads is what parallel authoring produces -- two revisions naming the
    same parent -- and `alembic upgrade head` then fails outright rather than
    picking one. Asserting *which* head is the second half: a chain with one head
    somewhere in E003's block would mean E006's revisions are unreachable.
    """
    heads = ScriptDirectory.from_config(build_config()).get_heads()

    assert list(heads) == [E006_HEAD], (
        f"the chain resolves to {sorted(heads)}; it must resolve to [{E006_HEAD!r}]. More "
        f"than one head means `alembic upgrade head` has no single destination; a different "
        f"single head means E006's revisions are not on the path to it."
    )


def test_every_declared_object_name_fits_postgresql_s_identifier_limit() -> None:
    """§Named Object Inventory: no name is silently truncated.

    PostgreSQL truncates an identifier past 63 bytes without complaint, and two
    truncated names can then collide. Every assertion in this suite that names a
    constraint would match nothing, while reporting that the constraint under
    test was never exercised -- if it reported anything at all.
    """
    too_long = sorted(
        name
        for name in (*E006_TABLES, E006_VIEW, *E006_INDEXES, *E006_CONSTRAINTS)
        if len(name.encode("utf-8")) > IDENTIFIER_BYTE_LIMIT
    )

    assert not too_long, (
        f"{too_long} exceed PostgreSQL's {IDENTIFIER_BYTE_LIMIT}-byte identifier limit and "
        f"would be truncated silently. Abbreviate the table part of the name, as E003 does "
        f"with `evcc`."
    )


# --------------------------------------------------------------------------- #
# VR-013, VR-014 -- applies from empty, and re-applies as a no-op
# --------------------------------------------------------------------------- #


def test_the_chain_applies_to_an_empty_database_through_e006_s_head(
    empty_scratch_database: URL,
) -> None:
    """VR-013: `migrate head` against a database with nothing in it.

    Run against a scratch database rather than the shared one: the shared
    database is already at head, so it would prove nothing, and it carries
    objects a fresh deployment will not have. This is the test that catches a
    revision depending on something no earlier one created -- E006's `0402`
    depends on E003's `chunk` and `extracted_value` and on E006's own `0401`,
    and `0403` on E003's `uq_extracted_value__id_source_count` -- which is
    invisible on any database that has been migrated incrementally.

    Asserted on the *tail* of the applied list rather than on the whole chain:
    E003's and E004's revisions running is `test_migration_chain.py`'s claim, and
    duplicating it here would make one defect fail two modules for two reasons.
    """
    applied = _migrate()

    assert applied[-len(E006_REVISIONS) :] == list(E006_REVISIONS), (
        f"the run ended with {applied[-len(E006_REVISIONS) :]}, not {list(E006_REVISIONS)}. "
        f"E006's revisions must be the last five to execute and must execute in the declared "
        f"order; a revision that exists but did not run is unreachable from the base."
    )
    assert _stamped_revisions(empty_scratch_database) == {E006_HEAD}, (
        f"the migrated database's alembic_version does not read {E006_HEAD!r}, so a later "
        f"`migrate` would try to re-run revisions that have already run."
    )

    missing = sorted(
        name
        for name in (*E006_TABLES, E006_VIEW)
        if not _relation_kinds(empty_scratch_database, name)
    )

    assert not missing, (
        f"{missing} do not exist after applying the chain to an empty database. The chain "
        f"reported success, so these were created by no revision that ran -- which is the "
        f"failure an already-migrated database cannot show."
    )


def test_reapplying_at_head_runs_no_e006_migration(empty_scratch_database: URL) -> None:
    """VR-014: a second `migrate head` performs no work.

    Two runs in one test, on one scratch database, because the claim is about the
    *second* one and it means nothing unless the first is known to have done
    something. Exit status is not the assertion -- `migrate head` exits zero
    whether it applied five migrations or none -- so this asserts on the list of
    migration bodies that actually executed, which must be empty.

    Idempotence here is Alembic's `alembic_version` bookkeeping doing its job.
    That is deliberate, and it is why no revision body carries its own "have I
    run yet?" guard: such a guard would make this test pass while the migration
    was in fact re-entered every time.
    """
    first_run = _migrate()

    assert first_run[-len(E006_REVISIONS) :] == list(E006_REVISIONS), (
        f"setup failed: the first run ended with {first_run[-len(E006_REVISIONS) :]}, not "
        f"{list(E006_REVISIONS)}. There is nothing to re-apply, so the claim below would hold "
        f"vacuously."
    )
    stamped_after_first_run = _stamped_revisions(empty_scratch_database)

    second_run = _migrate()

    assert second_run == [], (
        f"re-running `migrate head` re-executed {second_run}. Re-application must run no "
        f"migration already applied (VR-014); every revision in this block issues bare "
        f"`CREATE` statements, so one that ran twice would fail on a redeploy rather than "
        f"quietly doing nothing."
    )
    assert _stamped_revisions(empty_scratch_database) == stamped_after_first_run, (
        "alembic_version changed during a no-op run, so the version bookkeeping does not "
        "agree with the work performed."
    )


# --------------------------------------------------------------------------- #
# §Named Object Inventory -- exactly these objects, in both directions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("relation_name", [*E006_TABLES, E006_VIEW])
def test_each_declared_relation_exists_with_the_declared_kind(
    db_session: Session, relation_name: str
) -> None:
    """§Named Object Inventory: seven tables and one view, and the view is a view.

    One test per name so a failure says which object is missing. The *kind* is
    asserted alongside existence because `v_active_ingestion_generation` being a
    table would mean the active-generation predicate is a snapshot somebody
    maintains rather than a projection that cannot drift.
    """
    expected_kind = "v" if relation_name == E006_VIEW else "r"
    rows = db_session.execute(text(RELATION_KIND_SQL), {"relation_name": relation_name})
    kinds = [kind for (kind,) in rows]

    assert kinds == [expected_kind], (
        f"{relation_name!r} is {kinds or 'absent'} in the migrated schema; the inventory "
        f"declares a {'view' if expected_kind == 'v' else 'table'}. Revisions 0400-0403 "
        f"create it, and every other epic's tests expect it by this name."
    )


def test_the_active_generation_view_projects_the_declared_columns(db_session: Session) -> None:
    """§`v_active_ingestion_generation`: the projection, in order.

    The view is where run attribution is obtained at all -- which chunker
    version, which embedding revision, which run produced the row in hand -- so a
    column dropped from it does not fail anything, it just leaves every consumer
    joining back to `ingestion_run` by hand and getting no error when it forgets.

    `status` is asserted *absent* by this same comparison, and deliberately:
    every row the view returns is active by construction, and carrying the column
    would invite a reader to filter on it again and conclude the view does not.
    """
    columns = tuple(
        name for (name,) in db_session.execute(text(VIEW_COLUMNS_SQL), {"view_name": E006_VIEW})
    )

    assert columns == E006_VIEW_COLUMNS, (
        f"{E006_VIEW} projects {columns}; `data-model.md` declares {E006_VIEW_COLUMNS}. The "
        f"order is part of the declaration because a consumer may read positionally."
    )


def test_the_indexes_on_e006_s_tables_are_exactly_the_declared_set(db_session: Session) -> None:
    """§Named Object Inventory: every declared index exists, and no other does.

    Both directions, because they catch different defects. A missing index is an
    invariant that is not enforced -- `ix_ingestion_run_document__single_active`
    is the *only* thing standing between a promotion that skipped its removal and
    two live generations of one document. An undeclared one is an object nobody
    reviewed: it may be redundant, it may be the wrong shape, and nothing outside
    this catalog says it should be there.
    """
    tables = {"tables": list(E006_TABLES)}
    observed = {name for (name,) in db_session.execute(text(E006_INDEXES_SQL), tables)}

    assert not (E006_INDEXES - observed), (
        f"{sorted(E006_INDEXES - observed)} are declared in §Named Object Inventory but do "
        f"not exist on E006's tables."
    )
    assert not (observed - E006_INDEXES), (
        f"{sorted(observed - E006_INDEXES)} exist on E006's tables but are not declared in "
        f"§Named Object Inventory. Add each one to the artifact with its purpose, or drop it "
        f"-- an undocumented index is an invariant nobody knows is load-bearing, and the "
        f"first person to find it slow will remove it."
    )


def test_the_constraints_on_e006_s_tables_are_exactly_the_declared_set(
    db_session: Session,
) -> None:
    """§Named Object Inventory: every declared constraint exists, with its kind.

    The largest of these enumerations and the one that matters most. §Conventions
    requires every constraint to be explicitly named precisely so a later forward
    migration can `DROP CONSTRAINT` it and a test can assert *which* rule rejected
    a row -- and both only work if the name is what the catalog actually holds.

    The kind is compared as well as the name: a `CHECK` replaced by a foreign key
    of the same name is a different rule wearing a reviewed name, and every test
    that expects a `CheckViolation` from it would then fail while reporting
    something else.
    """
    tables = {"tables": list(E006_TABLES)}
    observed = {name: kind for name, kind in db_session.execute(text(E006_CONSTRAINTS_SQL), tables)}

    missing = sorted(set(E006_CONSTRAINTS) - set(observed))
    undeclared = sorted(set(observed) - set(E006_CONSTRAINTS))
    wrong_kind = sorted(
        f"{name}: declared {E006_CONSTRAINTS[name]!r}, found {observed[name]!r}"
        for name in set(E006_CONSTRAINTS) & set(observed)
        if observed[name] != E006_CONSTRAINTS[name]
    )

    assert not missing, (
        f"{missing} are declared in §Named Object Inventory but do not exist. Each one is a "
        f"rule the epic is written against; a missing CHECK is a domain nothing enforces."
    )
    assert not undeclared, (
        f"{undeclared} exist on E006's tables but are not declared in §Named Object "
        f"Inventory. A constraint that is not documented cannot be referenced by a later "
        f"migration or expected by another epic's tests."
    )
    assert not wrong_kind, (
        f"{wrong_kind}. The name is reviewed; the rule behind it is not the reviewed one."
    )
