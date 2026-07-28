"""DV-026: E007 declares exactly one function, and calls the rest.

`data-model.md` § Conventions says the three delivered array helpers —
`fn_is_sorted_ascending`, `fn_is_non_increasing`, `fn_all_within_unit_interval` —
"are called from E007's checks and not re-declared. A second helper with the same
body would be a second thing to keep in step." That is a convention, and a
convention with no assertion under it is a preference. This module is the
assertion.

**Why the delivered audit does not cover it.** E003's
`test_every_function_is_named_in_the_data_model` requires every non-extension
function to appear in some epic's data model. A re-declared helper would satisfy
it by being documented — the audit checks that a function is *named*, not that it
is *new*. So the failure DV-026 exists to catch is invisible there, and it is a
quiet one: two copies of an array invariant, both correct on the day they are
written, and a later strengthening applied to one of them.

**Two halves, and the second is what closes the first's escape route.**

* Over the **revision sources**, attributed by prefix block: `0300`–`0399` is
  E007's, so a `CREATE FUNCTION` in one of those files is E007 declaring a
  function and a `CREATE FUNCTION` anywhere else is not. Exactly one is
  permitted, and it is `fn_vendor_shrinkage_wellformed`.
* Over **`pg_proc` in the live database**: every non-extension function in
  `public` must be one some schema source literally declares, and every function
  a schema source declares must exist. That reconciliation is what makes the
  source scan trustworthy rather than merely suggestive. A function created by an
  E007 revision through SQL the scan cannot see — assembled at runtime, or
  executed from a constant defined in another module — would appear in the
  catalog with nothing declaring it, and fails there.

The residual, stated rather than left implicit: a revision that assembled the
words `CREATE` and `FUNCTION` separately, *and* used a name some other source
file already declares, would evade both halves. That is a re-declaration of a
delivered helper under its own name, and the check that would catch it is one
that migrates a scratch database to `0300`'s parent and again to head and
compares the two function sets. It is not implemented here because it costs a
full second application of the chain per run to close a route that requires
deliberate obfuscation, and because the cheaper structural bar — E007's
revisions may not import `model.schema.helpers`, where the delivered DDL
constants live — closes the only plausible way in.

**Why this lives in the forecast tier and not beside E003's audits.** It is a
claim about *which epic declared what*, and the epic making the claim is the one
that has to keep it true. E003's suite has no reason to assert E007's restraint.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

#: `src/model/tests/forecast/` -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[4]

#: The schema package. Every `CREATE FUNCTION` in the project lives under here:
#: four in `helpers.py`, which `0007` and `0008` execute, and two inline in
#: revisions (`0003`'s `fn_all_sha256_prefixed` and `0300`'s
#: `fn_vendor_shrinkage_wellformed`). Scanning this tree and no wider is
#: deliberate — a function created from outside the schema package is a DDL path
#: the migration chain does not own, and must surface as an unexplained catalog
#: entry rather than be absorbed into the expected set.
SCHEMA_PACKAGE = REPO_ROOT / "src" / "model" / "src" / "model" / "schema"
VERSIONS_DIR = SCHEMA_PACKAGE / "versions"

#: E007's reserved filename-prefix block, inclusive. The prefix is the only
#: thing that attributes a revision to an epic ({SAD:ADR-0013}); there is no
#: manifest and no per-file marker.
E007_BLOCK = (300, 399)

#: The one function this epic is permitted to declare.
E007_FUNCTION = "fn_vendor_shrinkage_wellformed"

#: The three delivered helpers E007's checks call. Named here because the
#: positive control below has to know what "called, not re-declared" is a claim
#: *about*: an epic that used none of them would satisfy "declares no second
#: helper" while proving nothing.
REUSED_HELPERS = ("fn_is_sorted_ascending", "fn_is_non_increasing", "fn_all_within_unit_interval")

#: The module holding E003's helper DDL as Python constants. An E007 revision
#: importing from it would be executing a delivered `CREATE FUNCTION` without
#: the words appearing in its own source, which is the one laundering route the
#: text scan cannot see.
HELPERS_MODULE = "model.schema.helpers"

#: `NNNN_name.py`. Same convention the two block checks use.
REVISION_FILENAME = re.compile(r"^(?P<prefix>\d{4})_[a-z0-9_]+\.py$")

#: `CREATE FUNCTION name(` and `CREATE OR REPLACE FUNCTION name(`. The optional
#: `OR REPLACE` is matched rather than ignored: `data-model.md` § Immutable
#: Helper Functions records that replacing a function in place does not
#: re-validate stored rows and is therefore forbidden, so a `CREATE OR REPLACE`
#: in an E007 revision is a violation this scan must *see* in order to report.
CREATE_FUNCTION = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?P<name>[a-z_][a-z0-9_]*)\s*\(",
    re.IGNORECASE,
)

#: An import of `model.schema.helpers`, in either spelling.
HELPERS_IMPORT = re.compile(
    rf"^\s*(?:from\s+{re.escape(HELPERS_MODULE)}\s+import|import\s+{re.escape(HELPERS_MODULE)}\b)",
    re.MULTILINE,
)

#: Non-extension functions in `public`. `prokind = 'f'` excludes aggregates,
#: window functions and procedures; the `pg_depend` clause is pgvector telling
#: us what is pgvector's, so the exclusion is derived rather than a name list
#: that would go stale on an extension upgrade.
NON_EXTENSION_FUNCTIONS_SQL = """
SELECT p.proname, p.provolatile, p.proisstrict, p.proparallel
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.prokind = 'f'
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend d
      WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
  )
ORDER BY p.proname
"""

#: The properties `data-model.md` § Immutable Helper Functions requires of a
#: function that is sound inside a `CHECK`, as `pg_proc` spells them.
VOLATILE_IMMUTABLE = "i"
PARALLEL_SAFE = "s"


def _schema_sources() -> list[Path]:
    """Every Python source in the schema package, excluding caches."""
    sources = sorted(
        path for path in SCHEMA_PACKAGE.rglob("*.py") if "__pycache__" not in path.parts
    )
    assert sources, (
        f"no Python sources were found under {SCHEMA_PACKAGE}, so every scan below covers "
        f"nothing and would pass vacuously. The schema package moved; repoint "
        f"SCHEMA_PACKAGE before trusting a pass here."
    )
    return sources


def _revision_files() -> list[Path]:
    """Every revision file in the shared versions directory, oldest name first."""
    files = sorted(path for path in VERSIONS_DIR.glob("*.py") if REVISION_FILENAME.match(path.name))
    assert files, f"no revision files were found in {VERSIONS_DIR}"
    return files


def _prefix(path: Path) -> int:
    match = REVISION_FILENAME.match(path.name)
    assert match is not None, f"unreachable: {path.name} passed the filter"
    return int(match.group("prefix"))


def _e007_revisions() -> list[Path]:
    """The revisions in E007's block, discovered rather than listed.

    Discovery matters here for the same reason it matters in
    `test_migration_chain.py`: a hardcoded `("0300", "0301", "0302", "0303")`
    would keep passing while a fifth E007 revision declaring a second function
    sat beside it, uncovered.
    """
    low, high = E007_BLOCK
    revisions = [path for path in _revision_files() if low <= _prefix(path) <= high]
    assert revisions, (
        f"no revision in E007's block {low:04d}-{high:04d} was found, so this module is "
        f"asserting a restraint nobody is exercising. Either the migrations have not "
        f"landed or the block moved."
    )
    return revisions


def _functions_declared_in(path: Path) -> list[str]:
    """Function names this source file declares with a literal `CREATE FUNCTION`."""
    return [
        match.group("name") for match in CREATE_FUNCTION.finditer(path.read_text(encoding="utf-8"))
    ]


def test_e007_declares_exactly_one_function_across_its_revisions() -> None:
    """DV-026, the source half: one `CREATE FUNCTION` in the whole block.

    Attributed by prefix, which is the only attribution this directory has. The
    assertion is on the *list* rather than on a count, so the failure names the
    function that should not be there and the file it is in — a count would say
    "expected 1, found 2" and leave the reader to find the second.
    """
    declared = {
        path.name: _functions_declared_in(path)
        for path in _e007_revisions()
        if _functions_declared_in(path)
    }

    assert declared == {"0300_forecast_run_provenance.py": [E007_FUNCTION]}, (
        f"E007's revisions declare {declared}; DV-026 permits exactly one function, "
        f"{E007_FUNCTION!r}, in `0300`. The three delivered array helpers are *called* by "
        f"this epic's checks and must never be re-declared: a second copy of an array "
        f"invariant is a second thing to keep in step, and a later strengthening applied "
        f"to one of them would leave the two stores enforcing different rules "
        f"(data-model.md § Conventions)."
    )


def test_no_e007_revision_imports_the_delivered_helper_ddl() -> None:
    """DV-026: the laundering route the text scan cannot see, closed structurally.

    `model.schema.helpers` holds E003's four `CREATE FUNCTION` statements as
    Python constants, and `0008` executes three of them. An E007 revision that
    imported one and executed it would re-declare a delivered helper with no
    `CREATE FUNCTION` appearing anywhere in its own source, so the scan above
    would report nothing.

    Nothing legitimate needs the import: the helpers are *called* from SQL inside
    a `CHECK`, by name, and a name in a string needs no Python symbol.
    """
    importers = sorted(
        path.name
        for path in _e007_revisions()
        if HELPERS_IMPORT.search(path.read_text(encoding="utf-8"))
    )

    assert not importers, (
        f"{importers} import {HELPERS_MODULE!r}, which holds the delivered helper DDL as "
        f"executable constants. Executing one of them would re-declare a function E007 is "
        f"supposed to call. E007's checks reference the helpers by name inside SQL and "
        f"need no import to do so (DV-026)."
    )


@pytest.mark.parametrize("helper", REUSED_HELPERS)
def test_e007_actually_calls_each_delivered_helper_it_claims_to_reuse(helper: str) -> None:
    """The positive control for the two tests above.

    "E007 declares no second helper" is satisfied trivially by an epic that never
    uses one. The convention DV-026 enforces is *reuse* — called, not
    re-declared — so the reuse has to be observed or the restraint is a claim
    about nothing. One test per helper, so a failure names which of the three
    stopped being called rather than reporting the set.
    """
    callers = sorted(
        path.name
        for path in _e007_revisions()
        if re.search(rf"\b{re.escape(helper)}\s*\(", path.read_text(encoding="utf-8"))
    )

    assert callers, (
        f"no E007 revision calls {helper!r}. Either an array invariant this epic's tables "
        f"are supposed to carry has been dropped, or it has been re-expressed inline — "
        f"and 'E007 re-declares nothing' then asserts nothing, because there is nothing it "
        f"was reusing (DV-026)."
    )


def test_every_non_extension_function_in_the_database_is_declared_by_a_schema_source(
    db_session: Session,
) -> None:
    """DV-026, the catalog half: the live function set reconciles with the sources.

    This is what makes the source scan evidence rather than a suggestion. It
    closes the direction the scan cannot: a function created by SQL the regex
    does not see — assembled at runtime, or executed from a constant defined
    outside the schema package — exists in `pg_proc` with nothing declaring it,
    and is reported here.

    Asserted as a set *equality*, in both directions on purpose. A function in
    the catalog and in no source is unexplained DDL. A function in a source and
    not in the catalog means the database is behind the chain, or that a
    revision's `CREATE FUNCTION` is written but never executed — a declaration
    every reader would take for an object that exists.
    """
    declared = {name for path in _schema_sources() for name in _functions_declared_in(path)}
    present = {row.proname for row in db_session.execute(text(NON_EXTENSION_FUNCTIONS_SQL))}

    assert present == declared, (
        f"the live non-extension function set disagrees with what the schema sources "
        f"declare. Only in the database: {sorted(present - declared)} — created by DDL no "
        f"source file declares, which is either a hand-issued statement against a shared "
        f"database or a revision assembling SQL the scan cannot read. Only in the sources: "
        f"{sorted(declared - present)} — declared but not present, so either the database "
        f"is behind the chain or the statement is never executed."
    )


def test_the_reconciled_set_attributes_exactly_one_function_to_this_epic(
    db_session: Session,
) -> None:
    """DV-026's claim, stated over the catalog rather than over the sources alone.

    The test above proves the catalog and the sources describe the same set; the
    first test proves E007's block declares one name. Composing them gives the
    quantity DV-026 is actually about — how many functions in the database exist
    *because of this epic* — without migrating a scratch database twice to
    measure a before-and-after delta that would report the same number.

    Stated as its own test rather than left as an inference, because a reader
    checking whether DV-026 is covered should find the sentence, not have to
    reassemble it from two others.
    """
    e007_declared = {name for path in _e007_revisions() for name in _functions_declared_in(path)}
    other_declared = {
        name
        for path in _schema_sources()
        if path not in _e007_revisions()
        for name in _functions_declared_in(path)
    }
    present = {row.proname for row in db_session.execute(text(NON_EXTENSION_FUNCTIONS_SQL))}

    assert e007_declared == {E007_FUNCTION}, (
        f"E007's block declares {sorted(e007_declared)}, not exactly {{{E007_FUNCTION!r}}}."
    )
    assert E007_FUNCTION in present, (
        f"{E007_FUNCTION!r} is declared by `0300` but is absent from the database. The "
        f"schema this tier is reading was not built by the chain, or the chain has not "
        f"reached `0300`."
    )
    assert E007_FUNCTION not in other_declared, (
        f"{E007_FUNCTION!r} is also declared outside E007's block, so the catalog entry "
        f"cannot be attributed to this epic and the count below means nothing."
    )
    assert len(present - other_declared) == 1, (
        f"the database holds {sorted(present - other_declared)} beyond what every other "
        f"epic's sources declare; DV-026 permits exactly one, {E007_FUNCTION!r}."
    )


def test_the_declared_function_is_immutable_strict_and_parallel_safe(
    db_session: Session,
) -> None:
    """The one function E007 does declare carries the properties a `CHECK` needs.

    `data-model.md` § Immutable Helper Functions requires `IMMUTABLE STRICT
    PARALLEL SAFE` — arguments only, no lookups, no `current_setting`, no
    collation-dependent comparison. Volatility is the half PostgreSQL will act
    on: a `CHECK` calling a non-immutable function is accepted at DDL time and
    then produces a constraint a dump-and-restore cannot re-prove, because the
    function may answer differently on the way back in.

    Read from `pg_proc` rather than from the revision's source text, because the
    declaration and the catalog can disagree — `CREATE OR REPLACE` in a later
    revision would change the catalog and leave `0300` reading as it always did.
    """
    rows = [
        row
        for row in db_session.execute(text(NON_EXTENSION_FUNCTIONS_SQL))
        if row.proname == E007_FUNCTION
    ]

    assert len(rows) == 1, (
        f"expected exactly one {E007_FUNCTION!r} in `public`; found {len(rows)}. More than "
        f"one is an overload, which would make 'the epic declares one function' true by "
        f"name and false by object."
    )
    function = rows[0]

    assert function.provolatile == VOLATILE_IMMUTABLE, (
        f"{E007_FUNCTION!r} has volatility {function.provolatile!r}, not "
        f"{VOLATILE_IMMUTABLE!r} (IMMUTABLE). A `CHECK` may call a non-immutable function "
        f"and PostgreSQL will accept the constraint, but a restore then re-proves it "
        f"against whatever the function says at that moment."
    )
    assert function.proisstrict, (
        f"{E007_FUNCTION!r} is not STRICT, so it is called with a NULL argument and must "
        f"then decide what a NULL shrinkage set means. `forecast_run.vendor_shrinkage` is "
        f"NOT NULL, so STRICT costs nothing and removes the question."
    )
    assert function.proparallel == PARALLEL_SAFE, (
        f"{E007_FUNCTION!r} is parallel mode {function.proparallel!r}, not "
        f"{PARALLEL_SAFE!r} (PARALLEL SAFE). Every function it calls is itself safe, so a "
        f"stricter mode here would be an unnecessary planner restriction on every query "
        f"that touches the constraint."
    )
