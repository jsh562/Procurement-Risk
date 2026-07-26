"""Whole-schema constraint audit: TR-039, TR-051, TR-063.

Three properties that are only checkable over the *whole* migrated object set,
which is why this module sits at the end of the chain rather than beside any one
table's tests. Each of the three is the kind of defect that a per-table test
cannot see, because the defect is the absence of something rather than the
presence of it.

* **TR-039 -- no check is silently satisfied by NULL.** A `CHECK` rejects a row
  only when its expression evaluates to *false*. `NULL` is not false, so a
  domain check on a nullable column accepts exactly the row it was written to
  refuse. The audit therefore asserts two things: every single-column check sits
  on a `NOT NULL` column, and every nullable column a check *does* touch is
  touched through a null-safe construct -- `coalesce`, an `IS NULL` / `IS NOT
  NULL` test, or `num_nonnulls`. Nullable-and-conditional columns are enumerated
  and their guard form asserted, never exempted: an exemption list is where this
  audit would go to die.

* **TR-051 -- what may be deferred, and what may not.** PostgreSQL cannot defer
  a `CHECK` or a `NOT NULL` at all, so "zero deferred checks" is not a design
  choice being honoured but a platform fact being evidenced -- and it *is*
  evidenced here, by asking the server to create one and recording its refusal,
  rather than asserted in a comment. Exactly one constraint in the schema is
  deferrable and it is a foreign key. Zero non-internal triggers, because the
  invariant map's whole claim is that every cross-row rule is carried
  declaratively.

* **TR-063 -- the declared defaults are exactly the enumerated set.** A default
  is a value the database invents on a writer's behalf, and every one of them is
  a place a caller can stop supplying a fact without anything failing. The set
  that is legitimate here is small and is named in `data-model.md`: the
  creation-timestamp columns and the active flag. The enumeration is *parsed
  from that artifact*, not restated here, so the artifact stays the single
  normative source (TR-083).

**Nothing in this module hardcodes a table list.** Every assertion enumerates
from `pg_catalog`, so a migration that lands after this file was written is
audited by it automatically -- which is the only version of a whole-schema audit
worth having.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import NamedTuple

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

#: `conftest.assert_rejects` as seen through its fixture.
RejectionAsserter = Callable[[Session, type[psycopg.Error], str], AbstractContextManager[None]]

#: `src/model/tests/schema/` -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_MODEL_PATH = REPO_ROOT / "specs" / "00002-core-data-schema" / "data-model.md"

#: The schema's single deferrable constraint (data-model.md §Conventions, TR-021).
THE_ONE_DEFERRABLE_CONSTRAINT = "fk_purchase_order_line__closing_event"

# --------------------------------------------------------------------------- #
# Catalog queries. Module-level constants, never assembled from values (S608).
# --------------------------------------------------------------------------- #

#: Every `CHECK` in the schema, with the columns it references and whether each
#: of those columns is `NOT NULL`. `conkey` is the set of columns PostgreSQL
#: itself extracted from the expression, so it cannot drift from the definition
#: text the way a hand-parsed column list would.
CHECK_CONSTRAINTS_SQL = """
SELECT
    cls.relname          AS table_name,
    con.conname          AS constraint_name,
    att.attname          AS column_name,
    att.attnotnull       AS column_is_not_null,
    cardinality(con.conkey) AS referenced_column_count,
    pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con
JOIN pg_class cls ON cls.oid = con.conrelid
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
JOIN LATERAL unnest(con.conkey) AS key(attnum) ON true
JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = key.attnum
WHERE nsp.nspname = 'public' AND con.contype = 'c'
ORDER BY table_name, constraint_name, column_name
"""

#: Every deferrable constraint of any kind, so the count is a claim about the
#: schema rather than about the one constraint we went looking for.
DEFERRABLE_CONSTRAINTS_SQL = """
SELECT con.conname, con.contype, con.condeferred, cls.relname
FROM pg_constraint con
JOIN pg_class cls ON cls.oid = con.conrelid
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
WHERE nsp.nspname = 'public' AND con.condeferrable
ORDER BY con.conname
"""

#: `tgisinternal` is set on the row-level triggers PostgreSQL creates to
#: implement referential actions. Those are foreign keys doing their job, not
#: procedural enforcement, and counting them would make the zero-trigger claim
#: unstatable.
USER_TRIGGERS_SQL = """
SELECT tg.tgname, cls.relname
FROM pg_trigger tg
JOIN pg_class cls ON cls.oid = tg.tgrelid
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
WHERE nsp.nspname = 'public' AND NOT tg.tgisinternal
ORDER BY cls.relname, tg.tgname
"""

#: Column defaults. `attgenerated = ''` excludes generated columns, whose
#: expressions `pg_attrdef` also stores -- a generated column is computed from
#: the row and is not a default a writer could have supplied instead.
COLUMN_DEFAULTS_SQL = """
SELECT
    cls.relname,
    att.attname,
    pg_get_expr(def.adbin, def.adrelid),
    format_type(att.atttypid, NULL)
FROM pg_attrdef def
JOIN pg_class cls ON cls.oid = def.adrelid
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
JOIN pg_attribute att ON att.attrelid = def.adrelid AND att.attnum = def.adnum
WHERE nsp.nspname = 'public' AND cls.relkind = 'r' AND att.attgenerated = ''
ORDER BY cls.relname, att.attname
"""

#: Generated columns, read separately so the exclusion above can be shown to be
#: an exclusion of something rather than a filter that matches nothing.
GENERATED_COLUMNS_SQL = """
SELECT cls.relname, att.attname
FROM pg_attribute att
JOIN pg_class cls ON cls.oid = att.attrelid
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
WHERE nsp.nspname = 'public' AND cls.relkind = 'r' AND att.attgenerated <> ''
ORDER BY cls.relname, att.attname
"""

#: Every column in the schema, used to tell "this documented default was
#: dropped" apart from "this documented default belongs to a table that has not
#: been created yet".
ALL_COLUMN_NAMES_SQL = """
SELECT DISTINCT att.attname
FROM pg_attribute att
JOIN pg_class cls ON cls.oid = att.attrelid
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
WHERE nsp.nspname = 'public' AND cls.relkind = 'r' AND att.attnum > 0 AND NOT att.attisdropped
"""

# --------------------------------------------------------------------------- #
# data-model.md parsing
# --------------------------------------------------------------------------- #

#: The Requirement Traceability row for TR-063. Every backticked name in it is a
#: column that may legitimately carry a default.
TR063_TRACEABILITY_ROW = re.compile(r"^\|\s*TR-063\s*\|(?P<carriers>.*)\|\s*$", re.MULTILINE)

#: The **Nullable-column checks** table. Its first column names every check that
#: is permitted to touch a nullable column.
#:
#: Case-sensitive, and anchored on "the complete list" rather than on the bold
#: run alone. `data-model.md` refers to this table by name from two other
#: places, so a looser pattern matches a cross-reference in the middle of the
#: `purchase_order_line` section and captures a body containing whichever check
#: names happen to appear in the prose between there and the next heading --
#: which reads as a partially-populated table rather than as a miss.
NULLABLE_CHECK_TABLE = re.compile(
    r"\*\*Nullable-column checks\*\*[^\n]*complete list"
    r"(?P<body>.*?)(?=\n\*\*Zero deferrable|\n## )",
    re.DOTALL,
)

BACKTICKED_IDENTIFIER = re.compile(r"`([a-z][a-z0-9_]*)`")

#: SQL string literals, stripped before any structural scan of a constraint
#: definition. A regex literal such as `'^[a-z0-9]+(-[a-z0-9]+)*$'` carries
#: parentheses and commas that would otherwise be read as expression structure.
#: `''` inside a literal is PostgreSQL's escape for a single quote.
SQL_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


class CheckReference(NamedTuple):
    """One (check constraint, referenced column) pair, as the catalog reports it."""

    table_name: str
    constraint_name: str
    column_name: str
    column_is_not_null: bool
    referenced_column_count: int
    definition: str


def _check_references(db_session: Session) -> list[CheckReference]:
    rows = [CheckReference(*row) for row in db_session.execute(text(CHECK_CONSTRAINTS_SQL))]
    assert rows, (
        "no CHECK constraints were found at all. Either the database is unmigrated or the "
        "catalog query is wrong; every assertion in this module would pass vacuously."
    )
    return rows


def _data_model_text() -> str:
    assert DATA_MODEL_PATH.is_file(), (
        f"{DATA_MODEL_PATH} is missing. It is this epic's normative artifact (TR-083) and "
        f"is the source of the enumerations this module audits against."
    )
    return DATA_MODEL_PATH.read_text(encoding="utf-8")


def _strip_string_literals(definition: str) -> str:
    """Blank out SQL string literals, preserving nothing but the quotes."""
    return SQL_STRING_LITERAL.sub("''", definition)


def _call_arguments(expression: str, function_name: str) -> list[list[str]]:
    """Top-level argument lists of every `function_name(...)` call in `expression`.

    A paren-balanced scan rather than a regex, because the arguments themselves
    contain calls -- `coalesce(array_length(fixture_hashes, 1), 0)` is the case
    that matters, and `[^)]*` would stop at the inner close paren and report the
    wrong first argument.
    """
    calls: list[list[str]] = []
    for match in re.finditer(rf"\b{function_name}\s*\(", expression, re.IGNORECASE):
        depth = 1
        start = index = match.end()
        arguments: list[str] = []
        while index < len(expression) and depth > 0:
            character = expression[index]
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    arguments.append(expression[start:index])
            elif character == "," and depth == 1:
                arguments.append(expression[start:index])
                start = index + 1
            index += 1
        calls.append(arguments)
    return calls


class NullGuards(NamedTuple):
    """How often a column appears in each null-safe position, and in total."""

    total: int
    null_tests: int
    coalesced: int
    null_safe_calls: int

    @property
    def value_positions(self) -> int:
        """Occurrences in a plain value position -- where NULL would propagate."""
        return self.total - self.null_tests - self.coalesced - self.null_safe_calls

    @property
    def is_guarded(self) -> bool:
        return bool(self.null_tests or self.coalesced or self.null_safe_calls)


def _null_guards(definition: str, column: str) -> NullGuards:
    """Classify every reference to `column` inside `definition`.

    Three constructs make a nullable reference safe, and each is counted
    separately so the failure message can say which one was expected:

    * `coalesce(col, ...)` -- and, importantly, `coalesce(f(col), ...)`, so the
      column is looked for anywhere inside the call's *first* argument. Mapping
      absent to a substitute is what turns a NULL comparison into a definite
      false, which is the only thing a `CHECK` acts on.
    * `col IS NULL` / `col IS NOT NULL` -- a null test is never NULL-valued.
    * `num_nonnulls(...)` -- null-safe by definition; it counts nulls rather
      than comparing against them.
    """
    expression = _strip_string_literals(definition)
    word = rf"\b{re.escape(column)}\b"

    coalesce_first_arguments = [
        arguments[0] for arguments in _call_arguments(expression, "coalesce") if arguments
    ]
    null_safe_call_arguments = [
        argument
        for arguments in _call_arguments(expression, "num_nonnulls")
        for argument in arguments
    ]

    return NullGuards(
        total=len(re.findall(word, expression)),
        null_tests=len(re.findall(rf"{word}\s+IS\s+(?:NOT\s+)?NULL", expression, re.IGNORECASE)),
        coalesced=sum(len(re.findall(word, argument)) for argument in coalesce_first_arguments),
        null_safe_calls=sum(
            len(re.findall(word, argument)) for argument in null_safe_call_arguments
        ),
    )


# --------------------------------------------------------------------------- #
# TR-039 -- no check silently satisfied by NULL
# --------------------------------------------------------------------------- #


def test_every_single_column_check_sits_on_a_not_null_column(db_session: Session) -> None:
    """TR-039 / SC-024: a range or domain check never guards a nullable column.

    "Range or domain check" is derived, not listed: a check whose `conkey` names
    exactly one column constrains that column's value domain and nothing else.
    Such a check on a nullable column is the classic vacuous constraint --
    `CHECK (confidence >= 0)` accepts a NULL confidence, and the row it was
    written to refuse is the row it lets through.

    Multi-column checks are a different animal and are audited by the two tests
    below: those are biconditionals and conditionals whose null branch is closed
    by a separate mechanism, and forbidding them outright would forbid the
    layer-conditional provenance rules the document table is built on.
    """
    unpaired = sorted(
        (row.table_name, row.constraint_name, row.column_name)
        for row in _check_references(db_session)
        if row.referenced_column_count == 1 and not row.column_is_not_null
    )

    assert not unpaired, (
        f"these single-column CHECKs guard a nullable column and are therefore satisfied "
        f"by a NULL in it: {unpaired}. A CHECK rejects only on false, and a comparison "
        f"against NULL is NULL. Add the NOT NULL, or -- if the column is deliberately "
        f"nullable -- rewrite the check into a conditional whose null branch is closed "
        f"and record it in data-model.md's Nullable-Column Checks table (TR-039)."
    )


def test_every_nullable_column_a_check_touches_is_touched_null_safely(
    db_session: Session,
) -> None:
    """TR-039: nullable-and-conditional columns are guarded, never exempted.

    The complement of the test above. Where a column is deliberately nullable,
    the check that governs it must still be a definite `true` or `false` on a
    null -- through `coalesce`, through an explicit null test, or through
    `num_nonnulls`. This enumerates every such pair from the catalog and states
    which guard each one carries, so a newly added conditional check is audited
    the moment it lands rather than being quietly absent from a list.
    """
    unguarded = {}
    for row in _check_references(db_session):
        if row.column_is_not_null:
            continue
        guards = _null_guards(row.definition, row.column_name)
        if not guards.is_guarded:
            unguarded[(row.constraint_name, row.column_name)] = " ".join(row.definition.split())

    assert not unguarded, (
        f"these checks reference a nullable column with no null-safe construct around it, "
        f"so they evaluate to NULL -- and are satisfied -- on exactly the row they exist "
        f"to refuse: {unguarded}. Wrap the column in coalesce(), test it with IS NULL / "
        f"IS NOT NULL, or count it with num_nonnulls (TR-039)."
    )


def test_a_nullable_column_used_as_a_value_is_wrapped_in_coalesce(db_session: Session) -> None:
    """TR-039: the coalesce mandate, stated as a rule rather than a list.

    A nullable column used as a *value* -- compared, trimmed, matched against a
    pattern -- propagates its NULL into the result. The remedy the schema uses
    throughout is `coalesce(col, <absent-marker>)`, which makes the comparison a
    definite false. The one legitimate alternative is a check that also tests
    the column with `IS NULL`, whose short-circuiting branch closes the null
    case before the value position is ever reached; those are recognised here
    rather than excused, and everything else must carry the coalesce.

    This is the assertion that would have caught `btrim(source_ref, ...) <> ''`
    accepting a `REAL` document with no source reference at all.
    """
    missing_coalesce = {}
    for row in _check_references(db_session):
        if row.column_is_not_null:
            continue
        guards = _null_guards(row.definition, row.column_name)
        if guards.value_positions > 0 and not guards.null_tests and not guards.coalesced:
            missing_coalesce[(row.constraint_name, row.column_name)] = " ".join(
                row.definition.split()
            )

    assert not missing_coalesce, (
        f"these checks compare a nullable column by value with neither a coalesce wrapper "
        f"nor an IS NULL branch: {missing_coalesce}. `NULL <> ''` is NULL, and a CHECK "
        f"accepts NULL. Rewrite as coalesce(col, '') so absence becomes a definite "
        f"false (TR-039)."
    )


def test_every_check_touching_a_nullable_column_is_recorded_in_the_data_model(
    db_session: Session,
) -> None:
    """TR-039 / TR-083: the Nullable-Column Checks table is complete.

    `data-model.md` publishes that table as the complete list of checks allowed
    to touch a nullable column, together with why each one's null branch is
    closed. A check that exists in the database and not in the table is an
    unreviewed exception to TR-039 -- the reasoning that makes it safe has not
    been written down, so nobody has had to make it.

    Asserted one-directionally. The table may legitimately name a check that
    does not exist yet, because a later migration in the chain creates it; what
    it may not do is fall silent about one that does.
    """
    artifact = _data_model_text()
    table = NULLABLE_CHECK_TABLE.search(artifact)

    assert table is not None, (
        "data-model.md no longer carries a **Nullable-column checks** table. It is the "
        "recorded justification for every check permitted to touch a nullable column; "
        "restore it rather than dropping this assertion (TR-039, TR-083)."
    )

    documented = set(BACKTICKED_IDENTIFIER.findall(table.group("body")))
    observed = {
        row.constraint_name for row in _check_references(db_session) if not row.column_is_not_null
    }
    undocumented = sorted(observed - documented)

    assert not undocumented, (
        f"{undocumented} touch a nullable column but are absent from data-model.md's "
        f"Nullable-Column Checks table. Add a row naming the check, the nullable column, "
        f"and why its null case is closed -- that reasoning is the review (TR-039)."
    )


# --------------------------------------------------------------------------- #
# TR-051 -- deferrability and triggers
# --------------------------------------------------------------------------- #


def test_no_check_constraint_is_deferrable(db_session: Session) -> None:
    """TR-051: zero deferrable `CHECK`s in the migrated schema.

    Read from `pg_constraint` rather than inferred from the migration sources,
    because the question is what the server holds. Kept as its own assertion
    even though the count test below subsumes it: a deferred check is a
    different and worse defect than an extra deferred foreign key, since a check
    that is not evaluated until commit cannot be attributed to the statement
    that violated it.
    """
    deferrable_checks = sorted(
        (name, table)
        for name, contype, _deferred, table in db_session.execute(text(DEFERRABLE_CONSTRAINTS_SQL))
        if contype == "c"
    )

    assert not deferrable_checks, (
        f"{deferrable_checks} are deferrable CHECK constraints. PostgreSQL does not "
        f"support deferring a CHECK, so their presence means the catalog is not what this "
        f"chain built (TR-051)."
    )


def test_postgresql_refuses_to_create_a_deferrable_check(db_session: Session) -> None:
    """TR-051, evidenced: the server rejects `CHECK ... DEFERRABLE` outright.

    The claim "no CHECK is deferred because PostgreSQL cannot defer one" is
    worth more as a demonstration than as a sentence in a document. This asks
    the running server -- the same digest-pinned image the application uses --
    to create one, and records the refusal: `FeatureNotSupported`, SQLSTATE
    0A000, "CHECK constraints cannot be marked DEFERRABLE".

    Runs inside the rolled-back session like everything else here, so the probe
    table never exists beyond this statement. It cannot be created in the first
    place, which is the point.
    """
    with pytest.raises(Exception) as raised:
        db_session.execute(
            text(
                "CREATE TABLE tmp_deferrable_check_probe ("
                "  value integer,"
                "  CONSTRAINT ck_probe CHECK (value > 0) DEFERRABLE INITIALLY DEFERRED"
                ")"
            )
        )

    original = getattr(raised.value, "orig", raised.value)
    assert isinstance(original, psycopg.errors.FeatureNotSupported), (
        f"expected PostgreSQL to refuse a DEFERRABLE CHECK with FeatureNotSupported "
        f"(SQLSTATE 0A000); it raised {type(original).__name__} instead. If the server "
        f"now accepts one, TR-051 stops being a platform fact and becomes a policy that "
        f"needs enforcing by other means."
    )


def test_postgresql_refuses_to_create_a_deferrable_not_null(db_session: Session) -> None:
    """TR-051, the other half: `NOT NULL ... DEFERRABLE` does not parse.

    A column `NOT NULL` in PostgreSQL 16 is not a `pg_constraint` row at all --
    it is `pg_attribute.attnotnull`, an attribute of the column with nowhere to
    record deferrability. So the catalog cannot report a deferred `NOT NULL`
    even in principle, and the only honest way to evidence "zero deferred NOT
    NULLs" is to show the grammar refuses the phrase. It does: a syntax error,
    SQLSTATE 42601, "misplaced DEFERRABLE clause".
    """
    with pytest.raises(Exception) as raised:
        db_session.execute(
            text("CREATE TABLE tmp_deferrable_not_null_probe (value integer NOT NULL DEFERRABLE)")
        )

    original = getattr(raised.value, "orig", raised.value)
    assert isinstance(original, psycopg.errors.SyntaxError), (
        f"expected PostgreSQL to refuse a DEFERRABLE NOT NULL as a syntax error (SQLSTATE "
        f"42601); it raised {type(original).__name__}. TR-051 rests on the grammar "
        f"forbidding this phrase."
    )


def test_exactly_one_constraint_in_the_schema_is_deferrable(db_session: Session) -> None:
    """TR-051 / data-model.md §Conventions: one deferrable constraint, and it is named.

    The count is the assertion. A second deferrable constraint would mean some
    invariant is no longer checked at the statement that violates it, and the
    test harness's whole isolation model -- an outer transaction that never
    commits -- cannot observe a deferred violation at all without
    `force_constraints_immediate`. So a silently added deferral does not merely
    weaken the schema; it makes some other test pass while proving nothing.
    """
    deferrable = [
        (name, contype, deferred, table)
        for name, contype, deferred, table in db_session.execute(text(DEFERRABLE_CONSTRAINTS_SQL))
    ]

    assert [row[0] for row in deferrable] == [THE_ONE_DEFERRABLE_CONSTRAINT], (
        f"the schema's deferrable constraints are {[row[0] for row in deferrable]}; exactly "
        f"one is permitted, {THE_ONE_DEFERRABLE_CONSTRAINT!r} (TR-051, TR-021). A new "
        f"deferral is not checked until COMMIT, which the schema test harness never "
        f"reaches -- see conftest.force_constraints_immediate."
    )

    name, contype, deferred, table = deferrable[0]
    assert contype == "f", (
        f"{name} is deferrable but has contype {contype!r}, not 'f'. The one permitted "
        f"deferral is a foreign key; anything else is a defect."
    )
    assert deferred, (
        f"{name} is DEFERRABLE INITIALLY IMMEDIATE. TR-021 requires INITIALLY DEFERRED: a "
        f"closed line and its terminal event are written in one transaction, and an "
        f"immediate check would refuse the line before its event exists."
    )
    assert table == "purchase_order_line", (
        f"{name} is declared on {table!r}, not purchase_order_line. The deferral belongs to "
        f"the closed-line invariant and nothing else."
    )


def test_the_schema_carries_no_triggers(db_session: Session) -> None:
    """TR-051 / SC-024: zero non-internal triggers.

    The invariant map's claim is that every cross-row rule is carried by a
    composite foreign key, a partial unique index, or a single-row check. A
    trigger would be a fourth mechanism with materially worse properties: it
    fires per row, it cannot be replaced in place, and a data-only restore with
    triggers disabled loads straight past it -- so the invariant would hold in
    the live database and silently not hold in a restored one.

    `tgisinternal` triggers are excluded because those are the referential
    actions of the foreign keys themselves.
    """
    triggers = sorted(db_session.execute(text(USER_TRIGGERS_SQL)))

    assert not triggers, (
        f"the schema declares {triggers}. data-model.md records zero triggers as a "
        f"delivered property (TR-051, invariant map row 13): the closing-line invariant "
        f"took the declarative rung, and the constraint-trigger fallback was not needed."
    )


# --------------------------------------------------------------------------- #
# TR-063 -- the declared defaults
# --------------------------------------------------------------------------- #


def _documented_default_carriers() -> set[str]:
    """The column names data-model.md permits to carry a default (TR-063)."""
    artifact = _data_model_text()
    row = TR063_TRACEABILITY_ROW.search(artifact)

    assert row is not None, (
        "data-model.md's Requirement Traceability table no longer carries a TR-063 row. "
        "That row is the enumeration of legitimate defaults; this audit has no expected "
        "set without it (TR-083)."
    )

    carriers = set(BACKTICKED_IDENTIFIER.findall(row.group("carriers")))

    assert carriers, (
        f"the TR-063 traceability row names no backticked column: {row.group('carriers')!r}. "
        f"With an empty enumeration every default would fail, which is a broken test rather "
        f"than a finding."
    )
    return carriers


def test_no_column_outside_the_enumerated_set_carries_a_default(db_session: Session) -> None:
    """TR-063: the declared defaults are exactly the ones data-model.md names.

    Two kinds of column may carry one, and both are creation facts the writer
    has no better claim to than the database does: the creation timestamps
    (`loaded_at`, `created_at`, `extracted_at`, `failed_at`, `added_at`) and the
    forecast run's `is_active` flag, which starts false because publication is a
    later, separate, atomic flip.

    Anything else is a default on a *domain* value, and those are exactly the
    defaults that hide a missing fact: a caller that forgets to supply a
    confidence, a horizon, or a source kind gets a plausible row instead of a
    rejection, and the row is indistinguishable afterwards from one that was
    supplied deliberately.
    """
    permitted = _documented_default_carriers()
    observed = [
        (table, column, expression, column_type)
        for table, column, expression, column_type in db_session.execute(text(COLUMN_DEFAULTS_SQL))
    ]

    assert observed, (
        "no column defaults were found at all. data-model.md declares several, so either "
        "the database is unmigrated or the query is wrong -- and the assertion below would "
        "hold vacuously."
    )

    undeclared = sorted(
        (table, column, expression)
        for table, column, expression, _type in observed
        if column not in permitted
    )

    assert not undeclared, (
        f"{undeclared} carry a column default that data-model.md's TR-063 row does not "
        f"permit ({sorted(permitted)}). A default on anything but a creation timestamp or "
        f"the active flag lets a caller omit a fact and still write a plausible row. "
        f"Remove it, or amend TR-063 with the reasoning that makes it legitimate."
    )


def test_every_enumerated_default_that_can_exist_does_exist(db_session: Session) -> None:
    """TR-063, the other direction: an enumerated default has not been dropped.

    Restricted to columns that actually exist. The enumeration covers the whole
    chain including migrations that may not have landed on this database yet, so
    a name with no column behind it is "not created yet" and not a finding --
    while a name that *is* a column and carries no default is a default someone
    removed.
    """
    permitted = _documented_default_carriers()
    existing_columns = {name for (name,) in db_session.execute(text(ALL_COLUMN_NAMES_SQL))}
    with_defaults = {
        column
        for _table, column, _expression, _type in db_session.execute(text(COLUMN_DEFAULTS_SQL))
    }

    dropped = sorted((permitted & existing_columns) - with_defaults)

    assert not dropped, (
        f"{dropped} exist as columns and are enumerated in data-model.md's TR-063 row, but "
        f"carry no default. Either the default was dropped -- in which case every writer "
        f"must now supply the value -- or the enumeration names a column it did not mean."
    )


def test_each_declared_default_is_the_expected_expression(db_session: Session) -> None:
    """TR-063: a creation timestamp defaults to `now()`, the active flag to `false`.

    Naming which columns may carry a default is only half the rule; a
    `created_at` defaulting to a fixed date, or an `is_active` defaulting to
    true, would pass the enumeration and still be wrong. The second would be
    materially wrong -- a run that publishes itself on insert defeats the
    single-active-run index's whole purpose, which is that publication is an
    explicit flip.
    """
    for table, column, expression, column_type in db_session.execute(text(COLUMN_DEFAULTS_SQL)):
        if column_type == "boolean":
            assert expression == "false", (
                f"{table}.{column} is a boolean defaulting to {expression!r}, not false. "
                f"An active flag that starts true publishes a run on insert."
            )
        else:
            assert expression == "now()", (
                f"{table}.{column} defaults to {expression!r}. Creation timestamps default "
                f"to now() and nothing else -- a constant would make every row claim the "
                f"same creation time (TR-063)."
            )


def test_generated_columns_are_not_counted_as_defaults(db_session: Session) -> None:
    """`pg_attrdef` stores both; only one of them is a default.

    A generated column's expression lives in the same catalog table as a
    default, so the audit above filters on `attgenerated`. This asserts the
    filter removes something: the schema has generated columns, they are absent
    from the default set, and therefore the enumeration test is not passing
    merely because the filter matched nothing.
    """
    generated = {
        (table, column) for table, column in db_session.execute(text(GENERATED_COLUMNS_SQL))
    }

    assert generated, (
        "no generated columns were found, so the attgenerated filter in the defaults query "
        "excludes nothing and this guard is inert. data-model.md declares three "
        "(chunk.search_vector and the two closing-event columns on purchase_order_line)."
    )

    defaults = {
        (table, column)
        for table, column, _expression, _type in db_session.execute(text(COLUMN_DEFAULTS_SQL))
    }
    leaked = sorted(generated & defaults)

    assert not leaked, (
        f"{leaked} are generated columns being reported as carrying a default. The TR-063 "
        f"enumeration would then have to name them, which would confuse a value the "
        f"database computes from the row with a value a writer omitted."
    )
