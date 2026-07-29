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
from sqlalchemy import URL, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from model.schema.cli import DEFAULT_REVISION, EXIT_OK, main

#: `src/model/tests/schema/` -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_MODEL_PATH = REPO_ROOT / "specs" / "00003-core-data-schema" / "data-model.md"

#: The Alembic revisions, which both epics author into ({SAD:ADR-0013}).
VERSIONS_DIR = REPO_ROOT / "src" / "model" / "schema" / "versions"
if not VERSIONS_DIR.is_dir():  # pragma: no cover - layout differs under an install
    VERSIONS_DIR = REPO_ROOT / "src" / "model" / "src" / "model" / "schema" / "versions"

#: This epic's reserved filename-prefix block, inclusive. {SAD:ADR-0013} leaves
#: `0100`-`0199` to E004; the numbers are a *claim*, never something the runner
#: compares to decide order.
OWN_BLOCK = (1, 99)

#: `CREATE TABLE [IF NOT EXISTS] name`, and the same for the relation kinds that
#: could carry another epic's design just as effectively. Written to match the
#: statement rather than the file, so a docstring explaining why a revision does
#: *not* create something is not mistaken for one that does.
CREATE_RELATION = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?"
    r"(?:TABLE|VIEW|MATERIALIZED\s+VIEW|UNLOGGED\s+TABLE)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?"
    r'"?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"?',
    re.IGNORECASE,
)

#: `NNNN_name.py`.
REVISION_FILENAME = re.compile(r"^(?P<prefix>\d{4})_")


def _data_model_paths() -> list[Path]:
    """Every epic's data model, this one included."""
    return sorted((REPO_ROOT / "specs").glob("*/data-model.md"))


def _revision_files() -> list[Path]:
    return sorted(
        path
        for path in VERSIONS_DIR.glob("*.py")
        if path.name != "__init__.py" and REVISION_FILENAME.match(path.name)
    )


def _is_own_block(path: Path) -> bool:
    match = REVISION_FILENAME.match(path.name)
    assert match is not None, f"unreachable: {path.name} passed the filter"
    low, high = OWN_BLOCK
    return low <= int(match.group("prefix")) <= high


def _revisions_creating(relation_name: str) -> list[Path]:
    """Revisions whose executed SQL creates a relation of this name."""
    return [
        path
        for path in _revision_files()
        if any(
            match.group("name") == relation_name
            for match in CREATE_RELATION.finditer(path.read_text(encoding="utf-8"))
        )
    ]


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
    """Every identifier inside code formatting in **every** epic's data model.

    Restricted to code spans deliberately. Matching the whole document would
    let a table called `document` pass on the strength of the English word, and
    the point of TR-083 is that the *object* was written down, not that its name
    is a word someone used.

    **Widened 2026-07-26 from this epic's document to every `specs/*/`
    data model, for the same reason the ownership test above was rescoped.**
    TR-083 makes a data model normative "for the whole object set", and while
    E003 was the sole author of the chain, this epic's document *was* the whole
    object set. {SAD:ADR-0013} put E004's revisions in the same directory
    against the same database, so the catalog now enumerates objects this
    document has no business declaring -- E004's `data-model.md` already
    declares them, in the epic that owns them.

    The requirement is unweakened: every object still has to be documented
    somewhere, by name, in a reviewed artifact. What changed is that "somewhere"
    is the set of epic data models rather than this one, so documenting an
    object is the owner's job instead of a duty that lands on whichever epic's
    tests run last. Requiring E003's document to carry E004's thirty-one
    constraints would invert the ownership {SAD:ADR-0013} exists to fix and
    duplicate a document that already exists.
    """
    assert DATA_MODEL_PATH.is_file(), (
        f"{DATA_MODEL_PATH} is missing. It is the normative artifact TR-083 makes this "
        f"module enforce; without it there is nothing to check the catalog against."
    )
    identifiers: set[str] = set()
    for path in _data_model_paths():
        for span in CODE_SPAN.findall(path.read_text(encoding="utf-8")):
            identifiers.update(IDENTIFIER.findall(span))

    assert len(identifiers) > 100, (
        f"only {len(identifiers)} identifiers were recovered from the data models' code "
        f"spans. The documents declare well over a hundred named objects, so this is a "
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
def test_a_table_owned_by_another_epic_was_not_created_here(relation_name: str, owner: str) -> None:
    """TR-036 / SC-017: no revision *in this epic's block* creates this name.

    One test per name rather than one test over a set, so a failure reports
    *which* boundary was crossed and to whose epic the design belongs.

    **Rescoped 2026-07-26 from the catalog to this epic's revision sources, and
    the reason is that the original premise stopped being true.** This test read
    `pg_class` and required the name to be absent from the migrated schema. That
    was a faithful implementation of "created here" only while E003 was the sole
    author of the chain. {SAD:ADR-0013} puts both epics' revisions in one
    directory against one database -- E003 in `0001`-`0099`, E004 in
    `0100`-`0199` -- so once E004's revisions landed, three of these six names
    existed in the migrated schema *because their owner created them*, which is
    the arrangement working rather than the boundary being crossed.

    The claim is unchanged and no weaker: this module's own docstring already
    stated the intent as "a migration in this chain that creates one has taken a
    decision that belongs to another epic's plan". Authorship was always what
    TR-036 was about; presence was a proxy that a shared chain invalidated. The
    scan reads only `0001`-`0099`, so an E003 revision creating `llm_invocation`
    still fails, which is the case the requirement exists for.

    Still asserted across relation *kinds*: declaring another epic's design as a
    view is the same encroachment as declaring it as a table.
    """
    offenders = sorted(
        path.name for path in _revisions_creating(relation_name) if _is_own_block(path)
    )

    assert not offenders, (
        f"{offenders} create {relation_name!r}, which belongs to {owner} "
        f"(TR-036, SC-017). Creating it in this epic's revision block fixes a "
        f"design decision that another epic's plan owns, and two revisions would "
        f"then both claim it."
    )


@pytest.mark.parametrize(
    ("relation_name", "owner"), sorted(OTHER_EPIC_TABLES.items()), ids=sorted(OTHER_EPIC_TABLES)
)
def test_an_other_epic_table_that_exists_was_created_by_a_revision_outside_this_block(
    db_session: Session, relation_name: str, owner: str
) -> None:
    """The other half of the rescoping, and what keeps it from being a loophole.

    Dropping the catalog assertion entirely would leave TR-036 blind to a table
    that exists for no attributable reason -- made by hand against a shared
    database, or by a revision since edited. So presence is still read from
    `pg_class`; what changed is the *conclusion* drawn from it. A name that
    exists must be traceable to a revision outside this epic's block, which is
    the positive form of "its owner created it".

    Absent names pass trivially and deliberately: E009's and E017's tables do
    not exist yet, and this test must not become a demand that they do.
    """
    exists = db_session.execute(text(NAMED_RELATION_SQL), {"relation_name": relation_name}).all()
    if not exists:
        return

    creators = sorted(
        path.name for path in _revisions_creating(relation_name) if not _is_own_block(path)
    )
    assert creators, (
        f"{relation_name!r} exists in the migrated schema as a "
        f"{', '.join(RELATION_KINDS.get(kind, kind) for (kind,) in exists)}, but no "
        f"revision outside this epic's block {OWN_BLOCK} creates it. It belongs to "
        f"{owner}, so either it was created by hand against a shared database, or "
        f"the revision that created it has been edited away (TR-036)."
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


# --------------------------------------------------------------------------- #
# E006 FR-065 / VR-015 -- the six tables E003 owns are untouched by E006
# --------------------------------------------------------------------------- #
#
# The mirror image of TR-036 above, and it belongs in this module for the same
# reason: it is a claim about the *boundary*, not about any one table. TR-036
# says E003 must not create another epic's design; FR-065 says E006 must not
# extend E003's. Both are ownership, read from opposite sides.
#
# E006 populates these six tables and adds seven of its own plus a view
# (`specs/00006-document-ingestion-and-extraction/data-model.md` §Scope). What it
# may not do is add a column, a constraint, or an index to any of them --
# {SAD:ADR-0017} makes E003's document normative for them, so an E006 revision
# widening `uq_chunk__document_ordinal` (say) would put two documents in
# disagreement with one catalog. That constraint's document scope is precisely
# what forced {SAD:ADR-0020}'s remove-then-write promotion, so "E006 did not
# simply widen it" is load-bearing rather than tidy.
#
# Asserted by *snapshot comparison across two revisions of the same database*,
# which is the only form that can see an addition made by any means. Reading the
# revision sources for `ALTER TABLE` would miss a `CREATE INDEX ... ON chunk`,
# and asserting a hardcoded expected catalog would restate E003's schema here and
# fail on every legitimate E003 amendment.

#: E006's spec Scope Excluded, VR-015. Named, not derived: the claim is about
#: these six designs specifically.
E003_OWNED_TABLES: tuple[str, ...] = (
    "chunk",
    "document",
    "extracted_value",
    "extracted_value_contributing_chunk",
    "extraction_failure",
    "field_vocabulary",
)

#: The revision the comparison starts from -- E007's head, and the last revision
#: before E006's block opens. Everything after it in the chain is E006's, so any
#: difference this test finds was introduced by an E006 revision and by nothing
#: else.
#:
#: **Moved from `0103` to `0303` on 2026-07-28.** It was E004's head while E006
#: chained directly onto `0103`; E007 claimed `0300`-`0399` concurrently and
#: landed first, so E006 renumbered to `0400`-`0499` and re-parented onto E007's
#: head. Left at `0103` this constant would put E007's four revisions inside the
#: window and attribute anything they changed to E006 -- which is the one thing
#: the sentence above promises it does not do.
OWNERSHIP_BOUNDARY_REVISION = "0303"

#: A relation E006 creates, used as the positive control. Without it, a chain
#: whose `04xx` revisions had all been deleted would satisfy the equality below
#: perfectly and prove nothing.
E006_PROBE_RELATION = "ingestion_run"

OWNED_COLUMNS_SQL = """
SELECT table_name, column_name, ordinal_position, data_type, udt_name,
       is_nullable, column_default, character_maximum_length,
       numeric_precision, numeric_scale, is_generated, generation_expression
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = ANY(:tables)
ORDER BY table_name, column_name
"""

#: Constraints are stored on the table they constrain (`conrelid`), so a foreign
#: key *from* an E006 table *to* one of these six is correctly invisible here:
#: it adds no object to the parent. What would show up is a constraint E006 put
#: on one of these tables, which is exactly what FR-065 forbids.
OWNED_CONSTRAINTS_SQL = """
SELECT cls.relname, con.conname, con.contype, pg_get_constraintdef(con.oid)
FROM pg_constraint con
JOIN pg_class cls ON cls.oid = con.conrelid
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
WHERE nsp.nspname = 'public' AND cls.relname = ANY(:tables)
ORDER BY cls.relname, con.conname
"""

OWNED_INDEXES_SQL = """
SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public' AND tablename = ANY(:tables)
ORDER BY tablename, indexname
"""

PROBE_RELATION_SQL = """
SELECT count(*)
FROM pg_class cls
JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
WHERE nsp.nspname = 'public' AND cls.relname = :relation_name
"""

#: The three catalog dimensions FR-065 names, in the order a failure reports
#: them: "zero columns, zero constraints, and zero indexes".
OWNERSHIP_DIMENSIONS = ("columns", "constraints", "indexes")


def _ownership_snapshot(url: URL) -> dict[str, list[tuple[object, ...]]]:
    """Columns, constraints, and indexes of the six E003-owned tables at `url`.

    A short-lived engine disposed immediately, for the reason the scratch
    fixture gives: the caller is about to migrate or drop this database, and a
    pooled connection left open would make either fail for a reason unrelated to
    what is under test.
    """
    engine = create_engine(url, poolclass=NullPool)
    tables = {"tables": list(E003_OWNED_TABLES)}
    try:
        with engine.connect() as connection:
            return {
                "columns": [
                    tuple(row) for row in connection.execute(text(OWNED_COLUMNS_SQL), tables)
                ],
                "constraints": [
                    tuple(row) for row in connection.execute(text(OWNED_CONSTRAINTS_SQL), tables)
                ],
                "indexes": [
                    tuple(row) for row in connection.execute(text(OWNED_INDEXES_SQL), tables)
                ],
            }
    finally:
        engine.dispose()


def _probe_relation_exists(url: URL, relation_name: str) -> bool:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text(PROBE_RELATION_SQL), {"relation_name": relation_name}
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_e006_adds_no_column_constraint_or_index_to_a_table_e003_owns(
    empty_scratch_database: URL,
) -> None:
    """E006 FR-065 / VR-015: the six tables are catalog-identical at `0303` and head.

    Driven on a scratch database because the comparison needs the schema at two
    revisions and the shared one is at head. `migrate 0303` stops the chain at
    E007's head; the snapshot taken there is the state E006 inherited. `migrate
    head` then runs `0400`-`0404` and the snapshot is taken again. Equality is
    the requirement, stated across all three dimensions FR-065 names.

    Two guards keep the equality from holding for the wrong reason. The
    before-snapshot must actually contain all six tables -- a query returning
    nothing would compare the empty set with itself -- and an E006 relation must
    exist afterwards, so the run being compared is one in which E006's revisions
    genuinely executed.

    Column *values* are out of scope and deliberately so: E006 writes rows into
    every one of these tables, and that is what the epic is for. What is asserted
    is that the *shape* it writes into is the shape E003 declared.
    """
    assert main([OWNERSHIP_BOUNDARY_REVISION]) == EXIT_OK, (
        f"`migrate {OWNERSHIP_BOUNDARY_REVISION}` failed against the scratch database, so "
        f"there is no inherited state to compare against."
    )

    before = _ownership_snapshot(empty_scratch_database)
    tables_present = {row[0] for row in before["columns"]}

    assert tables_present == set(E003_OWNED_TABLES), (
        f"at revision {OWNERSHIP_BOUNDARY_REVISION} the catalog reports "
        f"{sorted(tables_present)} of the six tables E003 owns, not "
        f"{sorted(E003_OWNED_TABLES)}. The comparison below would be between two "
        f"partial snapshots and would pass without having looked at the missing tables."
    )

    assert main([DEFAULT_REVISION]) == EXIT_OK, (
        "`migrate head` failed against the scratch database, so the after-snapshot would "
        "be taken at the same revision as the before-snapshot."
    )

    assert _probe_relation_exists(empty_scratch_database, E006_PROBE_RELATION), (
        f"{E006_PROBE_RELATION!r} does not exist after `migrate head`, so E006's revisions "
        f"did not run and the equality below holds because nothing happened between the two "
        f"snapshots."
    )

    after = _ownership_snapshot(empty_scratch_database)

    added = {
        dimension: sorted(set(after[dimension]) - set(before[dimension]))
        for dimension in OWNERSHIP_DIMENSIONS
    }
    removed = {
        dimension: sorted(set(before[dimension]) - set(after[dimension]))
        for dimension in OWNERSHIP_DIMENSIONS
    }

    added_report = {dimension: rows for dimension, rows in added.items() if rows}
    removed_report = {dimension: rows for dimension, rows in removed.items() if rows}

    assert not any(added.values()), (
        f"E006's revisions added {added_report} to tables E003 "
        f"owns. FR-065 and VR-015 allow zero columns, zero constraints, and zero indexes on "
        f"`document`, `chunk`, `field_vocabulary`, `extracted_value`, "
        f"`extracted_value_contributing_chunk`, and `extraction_failure`: {{SAD:ADR-0017}} "
        f"makes E003's data-model.md normative for them, so an addition here puts two "
        f"documents in disagreement with one catalog. Declare the object on a table E006 "
        f"owns, or raise an E003 amendment."
    )
    assert not any(removed.values()), (
        f"E006's revisions removed {removed_report} from tables "
        f"E003 owns. That is the same boundary crossed in the other direction, and it is "
        f"worse: a dropped constraint takes an invariant with it silently."
    )
