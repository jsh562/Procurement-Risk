"""What this epic built, and nothing else: TR-036 and TR-083.

Two claims about the migrated object set as a whole, which is why this module
sits at the end of the chain. Both are boundary claims -- they are about what is
*absent* and about what is *unaccounted for* -- and neither can be made from
inside any one table's test file.

* **TR-036 / SC-017 -- six named tables belong to other epics and must not
  appear here.** E003 owns the core schema; E004 owns the invocation record and
  the two price tables, E009 owns candidate pairing and the review
  queue, E017 owns criticality overrides. Each is asserted **by name**, not by
  an ownership predicate. A predicate ("no table outside the documented set")
  would be a weaker claim wearing a stronger one's clothes: it fails only once
  the encroaching table has been created, and it fails identically for a typo.
  Naming them makes the assertion say what it means -- these six specific
  designs are somebody else's, and a migration in this chain that creates one
  has taken a decision that belongs to another epic's plan.

* **TR-083 -- every created object appears in `data-model.md`.** That document
  declares itself normative for every table, column, named constraint, index,
  seeded row, and state transition. TR-083 is the sentence; this is the
  enforcement. Tables, views, constraints, indexes, and functions are all
  enumerated from `pg_catalog` and each is required to be named in the artifact.
  An object nobody documented is an object nobody reviewed, and the failure mode
  is not cosmetic: later epics reference these names verbatim, so a constraint
  the document does not carry is a constraint another epic's tests cannot know
  to expect.

**Two derived exclusions, and why neither weakens the claim.**

`CREATE EXTENSION vector` (migration `0001`) brings roughly 120 functions,
several types, and their operators with it. Those objects are pgvector's, not
this schema's, and enumerating them into `data-model.md` would bury the five
functions the schema actually declares. They are excluded by asking `pg_depend`
which objects an extension owns -- a derivation, never a name list -- and the
extension itself is required to be documented, so the thing that brought them in
is still accounted for.

Everything else, including Alembic's own `alembic_version` bookkeeping table, is
in scope and is required to be named in the artifact.

**No table list is hardcoded anywhere in this module** beyond the six names
TR-036 exists to forbid. Every positive enumeration comes from the catalog, so a
migration authored after this file is audited by it the moment it lands.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

#: `src/model/tests/schema/` -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_MODEL_PATH = REPO_ROOT / "specs" / "00002-core-data-schema" / "data-model.md"

#: TR-036 / SC-017. Named, with the epic each belongs to, because the point of
#: the requirement is the ownership boundary and not merely a count of tables.
OTHER_EPIC_TABLES: dict[str, str] = {
    "llm_invocation": "E004 — model invocation records",
    "price_table_version": "E004 — provider price table versions",
    "price_table_entry": "E004 — dated rates within a price table version",
    "candidate_pair": "E009 — entity-resolution candidates awaiting adjudication",
    "review_queue": "E009 — pairs routed to human review",
    "criticality_override": "E017 — per-line criticality overrides",
}

#: The extension migration `0001` enables. Named here only so the test can
#: require it to be documented; the objects it owns are discovered, not listed.
REQUIRED_EXTENSION = "vector"

# --------------------------------------------------------------------------- #
# Catalog queries. Module-level constants, never assembled from values (S608).
# --------------------------------------------------------------------------- #

#: Relations of every kind, minus anything an extension owns. `deptype = 'e'`
#: is the dependency PostgreSQL records from an object to the extension that
#: created it, so this is pgvector telling us what is pgvector's.
RELATIONS_SQL = """
SELECT cls.relkind, cls.relname
FROM pg_class cls
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
WHERE nsp.nspname = 'public'
  AND cls.relkind IN ('r', 'p', 'v', 'm', 'i', 'S')
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend dep
      WHERE dep.classid = 'pg_class'::regclass AND dep.objid = cls.oid AND dep.deptype = 'e'
  )
ORDER BY cls.relkind, cls.relname
"""

CONSTRAINTS_SQL = """
SELECT con.contype, con.conname
FROM pg_constraint con
JOIN pg_class cls ON cls.oid = con.conrelid
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
WHERE nsp.nspname = 'public'
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend dep
      WHERE dep.classid = 'pg_constraint'::regclass AND dep.objid = con.oid AND dep.deptype = 'e'
  )
ORDER BY con.conname
"""

FUNCTIONS_SQL = """
SELECT pro.proname
FROM pg_proc pro
JOIN pg_namespace nsp ON nsp.oid = pro.pronamespace
WHERE nsp.nspname = 'public'
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend dep
      WHERE dep.classid = 'pg_proc'::regclass AND dep.objid = pro.oid AND dep.deptype = 'e'
  )
ORDER BY pro.proname
"""

#: Objects an extension *does* own, read back so the exclusion above can be
#: shown to exclude something rather than to match nothing.
EXTENSION_OWNED_COUNT_SQL = """
SELECT count(*)
FROM pg_depend dep
JOIN pg_extension ext ON ext.oid = dep.refobjid
WHERE dep.deptype = 'e' AND ext.extname = :extension_name
"""

INSTALLED_EXTENSIONS_SQL = "SELECT extname FROM pg_extension ORDER BY extname"

#: TR-036 is asserted against every relation kind, not only ordinary tables: a
#: view or a materialized view named `review_queue` would encroach on E009's
#: design just as surely as a table would.
NAMED_RELATION_SQL = """
SELECT cls.relkind
FROM pg_class cls
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
WHERE nsp.nspname = 'public' AND cls.relname = :relation_name
"""

#: How `pg_class.relkind` and `pg_constraint.contype` read in a failure message.
RELATION_KINDS = {
    "r": "table",
    "p": "partitioned table",
    "v": "view",
    "m": "materialized view",
    "i": "index",
    "S": "sequence",
}
CONSTRAINT_KINDS = {
    "c": "check constraint",
    "f": "foreign key",
    "p": "primary key",
    "u": "unique constraint",
    "t": "constraint trigger",
    "x": "exclusion constraint",
}

#: A run of backtick-delimited text -- inline code or a fenced block. The
#: artifact writes object names inside these, often alongside the rest of the
#: declaration (`ck_pol__quantity_positive CHECK (quantity > 0)`), so membership
#: is "this identifier appears somewhere inside code formatting" rather than
#: "this identifier is the whole span".
CODE_SPAN = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _documented_identifiers() -> set[str]:
    """Every identifier appearing inside code formatting in `data-model.md`.

    Restricted to code spans deliberately. Matching the whole document would
    let a table called `document` pass on the strength of the English word, and
    the point of TR-083 is that the *object* was written down, not that its name
    is a word someone used.
    """
    assert DATA_MODEL_PATH.is_file(), (
        f"{DATA_MODEL_PATH} is missing. It is the normative artifact TR-083 makes this "
        f"module enforce; without it there is nothing to check the catalog against."
    )
    artifact = DATA_MODEL_PATH.read_text(encoding="utf-8")
    identifiers: set[str] = set()
    for span in CODE_SPAN.findall(artifact):
        identifiers.update(IDENTIFIER.findall(span))

    assert len(identifiers) > 100, (
        f"only {len(identifiers)} identifiers were recovered from data-model.md's code "
        f"spans. The document declares well over a hundred named objects, so this is a "
        f"parsing failure and every assertion below would fail for the wrong reason."
    )
    return identifiers


def _undocumented(objects: list[tuple[str, str]], kinds: dict[str, str]) -> list[str]:
    """`(kind_code, name)` pairs whose name is absent from the artifact."""
    documented = _documented_identifiers()
    return sorted(
        f"{kinds.get(kind, kind)} {name!r}" for kind, name in objects if name not in documented
    )


# --------------------------------------------------------------------------- #
# TR-036 -- the six tables that belong to other epics
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("relation_name", "owner"), sorted(OTHER_EPIC_TABLES.items()), ids=sorted(OTHER_EPIC_TABLES)
)
def test_a_table_owned_by_another_epic_was_not_created_here(
    db_session: Session, relation_name: str, owner: str
) -> None:
    """TR-036 / SC-017: this name does not exist in the migrated schema.

    One test per name rather than one test over a set, so a failure reports
    *which* boundary was crossed and to whose epic the design belongs. Asserted
    over `pg_class` rather than over `pg_tables`, so a view or a materialized
    view carrying the name is caught too -- encroaching on another epic's design
    by declaring its shape as a view is the same encroachment.
    """
    found = db_session.execute(text(NAMED_RELATION_SQL), {"relation_name": relation_name}).all()

    assert not found, (
        f"the migrated schema contains {relation_name!r} as a "
        f"{', '.join(RELATION_KINDS.get(kind, kind) for (kind,) in found)}. That object "
        f"belongs to {owner} (TR-036, SC-017). Creating it here fixes a design decision "
        f"that another epic's plan owns, and two chains would then both claim it."
    )


def test_the_other_epic_table_names_are_a_meaningful_set(db_session: Session) -> None:
    """A guard on the test above: the probe query can actually find a relation.

    Six assertions that a name is absent prove nothing if the query returns
    nothing for every input. This runs the same query against a relation that is
    known to exist and requires a hit, so an absent result above means absent.
    """
    found = db_session.execute(
        text(NAMED_RELATION_SQL), {"relation_name": "schema_constants"}
    ).all()

    assert [kind for (kind,) in found] == ["r"], (
        f"the probe query reports schema_constants as {found}, not a single ordinary "
        f"table. The TR-036 assertions use this query and would pass vacuously."
    )


# --------------------------------------------------------------------------- #
# TR-083 -- nothing exists that the artifact does not name
# --------------------------------------------------------------------------- #


def test_every_relation_is_named_in_the_data_model(db_session: Session) -> None:
    """TR-083: every table, view, index, and sequence appears in `data-model.md`.

    Indexes are in scope and are not an afterthought. `ix_forecast_run__single_active`
    is a partial unique index and is the *entire* mechanism behind "at most one
    active run" (invariant 17) -- an index nobody documented is an invariant
    nobody knows is load-bearing, and the first person to find it slow will drop
    it.
    """
    relations = [(kind, name) for kind, name in db_session.execute(text(RELATIONS_SQL))]

    assert relations, (
        "no relations were found outside the extension. The database is unmigrated or the "
        "exclusion is over-broad; this assertion would hold vacuously."
    )

    undocumented = _undocumented(relations, RELATION_KINDS)

    assert not undocumented, (
        f"{undocumented} exist in the schema but are not named anywhere in data-model.md. "
        f"TR-083 makes that document normative for the whole object set: add each one with "
        f"a one-line description rather than treating the omission as harmless."
    )


def test_every_constraint_is_named_in_the_data_model(db_session: Session) -> None:
    """TR-083: every named constraint appears in `data-model.md`.

    This is the largest of the three enumerations and the one that matters most.
    §Conventions requires every constraint to be explicitly named precisely so a
    later forward migration can `DROP CONSTRAINT` it and a test can assert
    *which* rule rejected a row -- and both of those only work if the name is
    written down somewhere a reader outside this epic can find it.
    """
    constraints = [(contype, name) for contype, name in db_session.execute(text(CONSTRAINTS_SQL))]

    assert constraints, (
        "no constraints were found outside the extension; the assertion below would hold vacuously."
    )

    undocumented = _undocumented(constraints, CONSTRAINT_KINDS)

    assert not undocumented, (
        f"{undocumented} exist in the schema but are not named anywhere in data-model.md. "
        f"A constraint that is not documented cannot be referenced by a later migration or "
        f"expected by another epic's tests (TR-083, §Conventions)."
    )


def test_every_function_is_named_in_the_data_model(db_session: Session) -> None:
    """TR-083: every function this schema declares appears in `data-model.md`.

    The extension's own functions are excluded by the `pg_depend` derivation, so
    what remains is exactly the set of `IMMUTABLE` helpers the schema declares
    to make element-wise array validation possible inside a `CHECK`. Each of
    those is a piece of enforcement logic whose *body* is invisible to a reader
    of the table definitions, which is why the artifact carries a summary of
    each one.
    """
    functions = [("f", name) for (name,) in db_session.execute(text(FUNCTIONS_SQL))]

    assert functions, (
        "no functions were found outside the extension. data-model.md declares five "
        "immutable helpers; finding none means the exclusion is over-broad."
    )

    undocumented = _undocumented(functions, {"f": "function"})

    assert not undocumented, (
        f"{undocumented} exist in the schema but are not named anywhere in data-model.md. "
        f"A CHECK records the identity of the function it calls, so an undocumented helper "
        f"is enforcement logic with no written specification (TR-083)."
    )


def test_the_extension_exclusion_is_derived_and_excludes_something(db_session: Session) -> None:
    """The exclusion above is load-bearing, so it is asserted rather than assumed.

    Two ways this could rot into a hole big enough to drive a table through. If
    `pg_depend` reported nothing as extension-owned, the exclusion would be
    inert and the three assertions above would be stronger than intended -- no
    harm. If it reported *everything*, they would be vacuous. This pins both
    ends: the extension is installed, it owns a substantial number of objects,
    and it is itself named in the artifact, so the thing that brought them in is
    documented even though they are not enumerated.
    """
    installed = {name for (name,) in db_session.execute(text(INSTALLED_EXTENSIONS_SQL))}

    assert REQUIRED_EXTENSION in installed, (
        f"the {REQUIRED_EXTENSION!r} extension is not installed ({sorted(installed)}). "
        f"Migration `0001` enables it (TR-006), and `chunk.embedding` cannot exist without "
        f"it -- so this database is not what the chain builds."
    )

    owned = db_session.execute(
        text(EXTENSION_OWNED_COUNT_SQL), {"extension_name": REQUIRED_EXTENSION}
    ).scalar_one()

    assert owned > 50, (
        f"the {REQUIRED_EXTENSION!r} extension owns only {owned} catalog objects. pgvector "
        f"brings well over a hundred; a number this low means the pg_depend derivation is "
        f"no longer finding them, and those objects are now being demanded of data-model.md."
    )

    assert REQUIRED_EXTENSION in _documented_identifiers(), (
        f"data-model.md does not name the {REQUIRED_EXTENSION!r} extension anywhere in code "
        f"formatting. Its objects are excluded from the enumerations above on the strength "
        f"of it being documented; if it is not, the exclusion is unaccounted for (TR-083)."
    )
