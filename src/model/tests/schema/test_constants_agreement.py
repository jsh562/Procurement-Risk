"""Drift control between `schema_constants` and the DDL literals it copies.

`schema_constants` publishes six values so that neither Python boundary has to
import the other to learn them (TR-043, TR-047). Two of those six also exist as
literals inside the DDL -- the dimension in `chunk.embedding vector(384)`, and
the tolerance inside `ck_line_posterior__residual_matches_grid_tail`. A
duplicated value that nothing compares is a value that will eventually disagree,
and the consequence is specific and silent: the serving boundary would compute
against a schema it does not have. An embedding built to a published 768 does
not fit a column declared 384 -- that one at least fails loudly on insert -- but
a published tolerance looser than the declared one produces a residual the
writer believes acceptable and the database rejects, and a *tighter* published
one silently narrows a bound the database never enforced.

**Direction of authority (TR-076, ADR-0013): the DDL literal governs and the
published row is the copy.** The revision that declares `chunk.embedding` cannot
read its dimension out of a table the same chain is still building, so the
literal is necessarily written first and the row necessarily restates it. It
follows that a drift failure in this module is repaired by **correcting the
`schema_constants` row in a new forward migration -- never by altering the
column or the constraint the literal declared**. Reversing that repair would
change the shape of stored data to match a bookkeeping row, which is exactly
backwards.

**Drift control is not value control, and TR-056 needs the second one.** Every
comparison above is between two copies of a number, which says nothing about
whether either copy is the number the epic decided on. TR-056 fixes three of the
six by name -- a 365-day survival horizon, 4,000 draws per run, and a
probability-sum tolerance of `1e-9` -- and two of those three have no DDL literal
to be compared against at all, so the published row is the only place in the
schema they appear. `test_the_seeded_row_carries_the_three_values_tr056_fixes`
asserts the values themselves for that reason. It is the one test here whose
expected numbers are literals, and deliberately so: resolving them from the row
under test would assert that a value equals itself.

**TR-079 -- the seeded reference data has no recovery path but the chain.** The
constants row and the 22 vocabulary rows are written by migrations `0002` and
`0005`, in the same revisions that create their tables, so neither table is ever
observable empty and there is no loader script to run or forget. Loss is
therefore *detected* here rather than repaired in place: if the row is gone, the
only supported recovery is re-applying the migration sequence against an empty
database.

**Why the `/src/api` assertion lives in this file.** It is the other half of the
same contract. `schema_constants` exists because the serving boundary must learn
these values *over the connection*; a Python import from `/src/api` into
`/src/model` would satisfy the same need while destroying the isolation the
table was created to protect. The manifest-level form of that rule -- which
entry may declare the database client at all -- is a cross-entry claim and lives
in `tests/checks/test_dependency_isolation.py` (TR-008, TR-042).
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

#: `conftest.assert_rejects` as seen through its fixture. Requested rather than
#: imported for the reason that fixture's docstring gives: the import form relies
#: on pytest having put this directory on `sys.path`.
RejectionAsserter = Callable[[Session, type[psycopg.Error], str], AbstractContextManager[None]]

#: `src/model/tests/schema/` -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_MODEL_PATH = REPO_ROOT / "specs" / "00003-core-data-schema" / "data-model.md"
API_ENTRY = REPO_ROOT / "src" / "api"

#: The two columns TR-048 names. Both are read from the catalog rather than
#: assumed; the names are the schema's published contract, not a table list, so
#: naming them here does not go stale when a later migration adds a table.
EMBEDDING_TABLE = "chunk"
EMBEDDING_COLUMN = "embedding"
TOLERANCE_CONSTRAINT = "ck_line_posterior__residual_matches_grid_tail"

#: Module-level SQL, never assembled from values (Ruff S608). `to_regclass`
#: rather than a `::regclass` cast so a missing table reports as a readable
#: assertion failure instead of an `UndefinedTable` two frames deep in psycopg.
DECLARED_COLUMN_TYPE_SQL = """
SELECT a.atttypmod, format_type(a.atttypid, a.atttypmod)
FROM pg_attribute a
WHERE a.attrelid = to_regclass('public.' || :table_name)
  AND a.attname = :column_name
  AND a.attnum > 0
  AND NOT a.attisdropped
"""

CONSTRAINT_DEFINITION_SQL = """
SELECT pg_get_constraintdef(c.oid)
FROM pg_constraint c
JOIN pg_namespace n ON n.oid = c.connamespace
WHERE n.nspname = 'public' AND c.conname = :constraint_name
"""

PUBLISHED_CONSTANTS_SQL = "SELECT * FROM schema_constants"
CONSTANTS_ROW_COUNT_SQL = "SELECT count(*) FROM schema_constants"
VOCABULARY_TERMS_SQL = "SELECT field_name, value_kind FROM field_vocabulary"

#: A numeric literal parenthesised and cast, as `pg_get_constraintdef` renders
#: one: `(0.000000001)::double precision`. Anchoring on the cast is what keeps
#: the match off the array subscripts and the operator constants in the same
#: expression.
DOUBLE_PRECISION_LITERAL = re.compile(
    r"\(\s*(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\)::double precision"
)

#: `### `field_vocabulary` — 22 rows, migration `0005`` and the table under it.
VOCABULARY_SECTION_HEADING = re.compile(
    r"^###\s+`field_vocabulary`\s+—\s+(\d+)\s+rows", re.MULTILINE
)
VOCABULARY_ROW = re.compile(
    r"^\|\s*`([a-z][a-z0-9_]*)`\s*\|\s*(text|number|date)\s*\|", re.MULTILINE
)

#: Where a seeded row may legitimately be written from. Package source trees
#: only: a test that inserts a vocabulary term inside the rolled-back session is
#: not a seeding path, and neither is one that deletes one.
PACKAGE_SOURCE_ROOTS = tuple(sorted((REPO_ROOT / "src").glob("*/src")))
MIGRATIONS_DIR = REPO_ROOT / "src" / "model" / "src" / "model" / "schema" / "versions"
SEED_INSERT_PATTERN = re.compile(r"INSERT\s+INTO\s+(schema_constants|field_vocabulary)\b", re.I)


def _published_constants(db_session: Session) -> dict[str, object]:
    """The single published row, as a mapping. Fails loudly if it is not single."""
    rows = db_session.execute(text(PUBLISHED_CONSTANTS_SQL)).mappings().all()
    assert len(rows) == 1, (
        f"`schema_constants` holds {len(rows)} rows; the whole table is defined to hold "
        f"exactly one (TR-043). Everything below reads that row, so there is nothing "
        f"meaningful to compare until this is repaired by re-applying the chain (TR-079)."
    )
    return dict(rows[0])


def _data_model_text() -> str:
    assert DATA_MODEL_PATH.is_file(), (
        f"{DATA_MODEL_PATH} is missing. It is the normative artifact for this epic "
        f"(TR-083); the seeded-vocabulary comparison below has no expected set without it."
    )
    return DATA_MODEL_PATH.read_text(encoding="utf-8")


def _imported_root_packages(source: str) -> set[str]:
    """Root package name of every import in `source`, absolute imports only.

    A relative import (`from . import x`) cannot reach another entry, so its
    level > 0 form is skipped rather than mis-attributed to a top-level package.
    """
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_published_dimension_equals_the_dimension_the_column_was_declared_with(
    db_session: Session,
) -> None:
    """TR-048 / SC-019: `vector_dimension` equals `chunk.embedding`'s real typmod.

    Read from `pg_attribute.atttypmod` rather than from `information_schema`,
    which has no column for a vector's dimension at all -- the extension type is
    outside the standard views, so the catalog is the only source.

    **pgvector stores the dimension in `atttypmod` directly, with no `- 4`
    adjustment.** That is not obvious and is worth stating, because the
    adjustment *is* required for the standard varying-length types: `varchar(n)`
    and `char(n)` store `n + 4`, the four bytes being the length header. Copying
    that convention onto a vector column would silently compare 384 against 380.
    The claim is verified here rather than trusted -- see the assertions below.
    """
    published = _published_constants(db_session)
    typmod, rendered = db_session.execute(
        text(DECLARED_COLUMN_TYPE_SQL),
        {"table_name": EMBEDDING_TABLE, "column_name": EMBEDDING_COLUMN},
    ).one()

    # The claim, checked against the catalog: PostgreSQL renders the declared
    # type from the same typmod it stores, so if `format_type` says
    # `vector(384)` while `atttypmod` says 384, the stored value *is* the
    # dimension and no adjustment applies.
    assert rendered == f"vector({typmod})", (
        f"{EMBEDDING_TABLE}.{EMBEDDING_COLUMN} renders as {rendered!r} but carries "
        f"atttypmod {typmod}. The two disagree, so atttypmod is not the declared "
        f"dimension for this type and the comparison below would be meaningless."
    )

    assert published["vector_dimension"] == typmod, (
        f"`schema_constants.vector_dimension` publishes "
        f"{published['vector_dimension']} but {EMBEDDING_TABLE}.{EMBEDDING_COLUMN} was "
        f"declared {rendered}. TR-076: the DDL literal governs. Repair this by "
        f"correcting the published row in a new forward migration -- do not alter the "
        f"column, which already holds data shaped to its declared dimension."
    )


def test_the_varchar_minus_four_convention_does_not_apply_to_the_vector_type(
    db_session: Session,
) -> None:
    """The control for the test above: a length-typed column that *does* add 4.

    `forecast_run.code_commit` is `char(40)` and carries typmod 44. Asserting
    both in one place is what turns "pgvector needs no `- 4`" from a comment
    into evidence: the two types are read the same way, out of the same column
    of the same catalog, and only one of them is offset.
    """
    typmod, rendered = db_session.execute(
        text(DECLARED_COLUMN_TYPE_SQL),
        {"table_name": "forecast_run", "column_name": "code_commit"},
    ).one()

    assert rendered == "character(40)", (
        f"this control assumes forecast_run.code_commit is char(40); it is {rendered!r}. "
        f"Point it at another fixed-length text column rather than deleting it."
    )
    assert typmod == 40 + 4, (
        f"char(40) reports atttypmod {typmod}, not 44. The +4 length-header convention "
        f"this control demonstrates no longer holds, so the contrast it draws with the "
        f"vector column is no longer evidence of anything."
    )


def test_published_tolerance_equals_the_literal_inside_the_residual_check(
    db_session: Session,
) -> None:
    """TR-048 / SC-019: `probability_sum_tolerance` equals the DDL literal.

    Parsed out of `pg_get_constraintdef` rather than out of the migration file,
    because the question is what the *database* enforces. A migration edited
    after it was applied would leave the two disagreeing and only the catalog
    would know.

    TR-076 again: the constraint governs. A mismatch is repaired by correcting
    the published row, never by loosening the check -- loosening it would widen
    an invariant to fit a bookkeeping value.
    """
    published = _published_constants(db_session)
    definition = db_session.execute(
        text(CONSTRAINT_DEFINITION_SQL), {"constraint_name": TOLERANCE_CONSTRAINT}
    ).scalar_one()

    literals = DOUBLE_PRECISION_LITERAL.findall(definition)

    assert len(literals) == 1, (
        f"expected exactly one double-precision literal in {TOLERANCE_CONSTRAINT}, found "
        f"{literals} in {definition!r}. With more than one there is no unambiguous "
        f"tolerance to compare against; with none, the check no longer carries the "
        f"constant this test exists to keep aligned."
    )

    assert float(literals[0]) == published["probability_sum_tolerance"], (
        f"`schema_constants.probability_sum_tolerance` publishes "
        f"{published['probability_sum_tolerance']!r} but {TOLERANCE_CONSTRAINT} enforces "
        f"{literals[0]}. TR-076: the DDL literal governs. Correct the published row in a "
        f"new forward migration; do not relax the constraint to match it."
    )


def test_the_constants_table_holds_exactly_one_row(db_session: Session) -> None:
    """TR-079 / invariant 24: one row, and the count is asserted, not assumed."""
    count = db_session.execute(text(CONSTANTS_ROW_COUNT_SQL)).scalar_one()

    assert count == 1, (
        f"`schema_constants` holds {count} rows. Migration `0002` seeds exactly one and "
        f"the table's structure admits no second (TR-043). A count of 0 means the seeded "
        f"row was deleted and is recoverable only by re-applying the chain against an "
        f"empty database (TR-079)."
    )


def test_a_second_constants_row_collides_on_the_primary_key(
    db_session: Session, assert_rejects: RejectionAsserter
) -> None:
    """TR-079: a duplicate row is refused by `pk_schema_constants`.

    `singleton` is a boolean primary key, so a second row carrying the same
    `true` is a key collision -- a `UniqueViolation`, and specifically *not* the
    singleton check, which this row satisfies. Naming the wrong constraint here
    would leave the check untested while the test stayed green, which is why
    `assert_rejects` requires the constraint name.
    """
    with assert_rejects(db_session, psycopg.errors.UniqueViolation, "pk_schema_constants"):
        db_session.execute(
            text(
                "INSERT INTO schema_constants ("
                "  singleton, vector_dimension, survival_horizon_days, draw_count,"
                "  probability_sum_tolerance, anchor_date_convention, percentile_convention"
                ") VALUES ("
                "  true, 384, 365, 4000, 1e-9, 'run_as_of_date',"
                "  'nearest_rank_one_based_no_interpolation'"
                ")"
            )
        )


def test_a_second_constants_row_carrying_false_is_refused_by_the_singleton_check(
    db_session: Session, assert_rejects: RejectionAsserter
) -> None:
    """TR-079: the other way in, and it trips a different constraint.

    A row with `singleton = false` does *not* collide on the primary key -- it
    is a distinct key value -- so the primary key alone would admit a second
    row. `ck_schema_constants__singleton` is what closes that door, and the two
    tests together are what make "a second row is impossible" a complete claim
    rather than half of one.
    """
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_schema_constants__singleton"
    ):
        db_session.execute(
            text(
                "INSERT INTO schema_constants ("
                "  singleton, vector_dimension, survival_horizon_days, draw_count,"
                "  probability_sum_tolerance, anchor_date_convention, percentile_convention"
                ") VALUES ("
                "  false, 384, 365, 4000, 1e-9, 'run_as_of_date',"
                "  'nearest_rank_one_based_no_interpolation'"
                ")"
            )
        )


def test_the_seeded_vocabulary_matches_the_normative_artifact(db_session: Session) -> None:
    """TR-079 / TR-083: the 22 seeded terms are exactly the ones data-model.md lists.

    The expected set is parsed out of the artifact rather than restated here,
    for the reason TR-083 exists: `data-model.md` is normative for the seeded
    rows, so a test carrying its own copy would let the two drift and would
    report the copy as correct. The count is parsed from the section heading
    too, so "22 rows" is checked against the table beneath it rather than
    against a number in this file.
    """
    artifact = _data_model_text()
    heading = VOCABULARY_SECTION_HEADING.search(artifact)

    assert heading is not None, (
        "data-model.md no longer carries a `### `field_vocabulary` — N rows` heading, so "
        "the expected seed set cannot be resolved. Restore the heading rather than "
        "hardcoding the terms here (TR-083)."
    )

    declared_count = int(heading.group(1))
    section = artifact[heading.end() : artifact.find("\n## ", heading.end())]
    expected = {(name, kind) for name, kind in VOCABULARY_ROW.findall(section)}

    assert len(expected) == declared_count, (
        f"data-model.md's `field_vocabulary` heading says {declared_count} rows but its "
        f"table lists {len(expected)}. The artifact disagrees with itself; fix it there."
    )

    seeded = {(name, kind) for name, kind in db_session.execute(text(VOCABULARY_TERMS_SQL))}

    assert seeded == expected, (
        f"the seeded vocabulary does not match data-model.md. Only in the database: "
        f"{sorted(seeded - expected)}. Only in the artifact: {sorted(expected - seeded)}. "
        f"A term is added by a new forward migration *and* recorded in the artifact; "
        f"neither half alone is complete (TR-044, TR-079, TR-083)."
    )


def test_no_seeding_path_exists_outside_the_migration_chain() -> None:
    """TR-079: the migrations are the only writer of the seeded reference data.

    This is the mechanical form of "recoverable only by re-applying the
    migration sequence". If some loader module also inserted these rows, the
    reference data would have two origins, only one of them versioned, and a
    database seeded by the other would pass every assertion above while being
    unreproducible from the chain.

    Scoped to the entries' packaged source trees. A test that inserts a
    vocabulary term inside the rolled-back session is exercising the schema, not
    seeding it, and is deliberately out of scope.
    """
    assert PACKAGE_SOURCE_ROOTS, (
        "no `src/*/src` package tree was found, so this scan covers nothing. The layout "
        "moved; repoint PACKAGE_SOURCE_ROOTS before trusting a pass here."
    )

    offenders = sorted(
        str(path.relative_to(REPO_ROOT))
        for root in PACKAGE_SOURCE_ROOTS
        for path in root.rglob("*.py")
        if MIGRATIONS_DIR not in path.parents
        and SEED_INSERT_PATTERN.search(path.read_text(encoding="utf-8"))
    )

    assert not offenders, (
        f"{offenders} insert into a seeded reference table from outside the migration "
        f"chain. TR-079 makes the chain the single origin of that data: re-applying the "
        f"sequence against an empty database must reproduce it exactly, which a second "
        f"writer defeats."
    )


def test_the_serving_boundary_reads_the_constants_rather_than_importing_them() -> None:
    """TR-047: nothing under `/src/api` imports `/src/model`.

    The whole reason `schema_constants` is a table is that the serving boundary
    must obtain these values without a Python dependency on the modeling
    boundary (ADR-0010, ADR-0013). An import would be the shortest path to the
    same six values and would pull the modeling stack into the serving
    resolution behind it.

    Asserted over the AST rather than by text search, so a name inside a comment
    or a docstring is not a violation and `import model.schema.url as _u` is.
    """
    api_sources = sorted((API_ENTRY / "src").rglob("*.py"))

    assert api_sources, (
        f"no Python sources were found under {API_ENTRY / 'src'}, so this scan asserts "
        f"nothing. The serving boundary's layout moved; repoint API_ENTRY."
    )

    importers = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in api_sources
        if "model" in _imported_root_packages(path.read_text(encoding="utf-8"))
    )

    assert not importers, (
        f"{importers} import the `model` package. `/src/api` must read the published "
        f"constants over the database connection (TR-047); importing them would make the "
        f"serving boundary depend on the modeling boundary, which ADR-0010 forbids."
    )


def test_the_serving_manifest_does_not_declare_the_modeling_boundary() -> None:
    """TR-047: the import above is not merely absent, it is unresolvable.

    A source scan proves nothing about a dependency that has been declared and
    simply not used yet -- and `/src/api` currently ships almost no code, so the
    scan above would pass over an entry that had already declared `model` as a
    dependency. Checking the manifest closes that window.
    """
    manifest = tomllib.loads((API_ENTRY / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {
        re.split(r"[<>=!~\[;\s]", spec, maxsplit=1)[0].strip().lower()
        for spec in manifest["project"].get("dependencies", [])
    }

    assert "model" not in declared, (
        f"`/src/api` declares the modeling boundary as a dependency ({sorted(declared)}). "
        f"Neither Python boundary may declare the other (ADR-0010); the constants are "
        f"read over the connection instead (TR-047)."
    )


@pytest.mark.parametrize(
    "column",
    [
        "vector_dimension",
        "survival_horizon_days",
        "draw_count",
        "probability_sum_tolerance",
        "anchor_date_convention",
        "percentile_convention",
    ],
)
def test_every_published_constant_is_readable_over_the_connection(
    db_session: Session, column: str
) -> None:
    """TR-043 / TR-047: all six values are present and non-null in the one row.

    The positive half of the import assertion. `/src/api` reading these over the
    connection is only a viable substitute for importing them if they are
    actually all there -- a NULL would send the reader looking for a fallback,
    and a fallback is a second copy of the constant by another name.
    """
    published = _published_constants(db_session)

    assert column in published, (
        f"`schema_constants` has no column {column!r}; the published set is "
        f"{sorted(published)}. Six constants are declared in data-model.md."
    )
    assert published[column] is not None, (
        f"`schema_constants.{column}` is NULL. Every constant column is NOT NULL by "
        f"declaration, so this indicates the table was rebuilt without its constraints."
    )


#: The three values TR-056 fixes by name: a survival horizon of 365 days, a
#: per-run draw count of 4000, and a probability-sum tolerance of 1e-9. Written as
#: literals because the requirement writes them as literals -- these are the
#: numbers the requirement *is*, and a test that resolved them from the row it is
#: checking, or from the DDL that seeded it, would assert only that a value equals
#: itself.
TR056_FIXED_VALUES: Mapping[str, float] = {
    "survival_horizon_days": 365,
    "draw_count": 4000,
    "probability_sum_tolerance": 1e-9,
}


@pytest.mark.parametrize(
    ("column", "expected"),
    list(TR056_FIXED_VALUES.items()),
    ids=list(TR056_FIXED_VALUES),
)
def test_the_seeded_row_carries_the_three_values_tr056_fixes(
    db_session: Session, column: str, expected: float
) -> None:
    """TR-056: the seeded row holds 365, 4000, and 1e-9 -- the values, not just non-nulls.

    The gap this closes is narrow and was easy to miss. Two tests already look at
    these columns and neither pins a value.
    `test_every_published_constant_is_readable_over_the_connection` asserts they
    are non-null, which a row of `1`, `1`, `0.5` would satisfy.
    `test_published_tolerance_equals_the_literal_inside_the_residual_check` asserts
    `probability_sum_tolerance` **agrees with the DDL literal** -- a drift check,
    and a real one, but it passes just as happily if the row and the constraint
    both say `1e-3`. Agreement between two copies is not the same claim as either
    copy being right, and TR-056 is the requirement that says which number is
    right.

    `survival_horizon_days` and `draw_count` have no DDL literal to be compared
    against at all -- `data-model.md` §Declared Constants records "none" for both,
    since each is carried per run in `forecast_run.horizon_days` and
    `.draw_count`. So for two of the three, this file's drift control had nothing
    to say and the published row was the only statement of the value anywhere in
    the schema. That is exactly the position TR-056 was written to fix: each of
    these three was *chosen* during planning rather than measured, and each is
    recorded in the data model as a scope decision with its evidence, reversal
    trigger, and production-scale alternative (Principle VII). A silently altered
    horizon would change every survival grid the modelling arm fits, and nothing
    in the suite would name it.

    Repairing a failure here means one of two things and never a third: either the
    seed in migration `0002` is wrong and a new forward migration corrects the row
    (TR-079 -- the row has no recovery path but the chain), or the *decision* has
    changed, in which case TR-056 and the scope-decision record change first and
    this test follows them. Editing the expected value to match the database would
    retroactively adjust a declared constant to fit the data, which Principle VII
    forbids.
    """
    published = _published_constants(db_session)

    assert published[column] == expected, (
        f"TR-056 fixes `{column}` at {expected!r}; the seeded row publishes "
        f"{published[column]!r}. Correct the row in a new forward migration if the seed "
        f"drifted, or amend TR-056 and the data model's scope-decision record first if the "
        f"value was deliberately changed -- never by editing the number expected here."
    )
