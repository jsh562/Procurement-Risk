"""T120 — NC-22 / SC-031 / DV-032(b): no `UPDATE` reaches an artifact store.

FR-034 writes every artifact row once. The grant half is `test_artifact_
immutability.py`'s, and under a superuser connection it restricts nothing
(E003 G-11), so the guarantee is carried by the **writer** — which makes the
absence of an `UPDATE` in `model.forecast` a claim about source text.

An absence check over source text is green when it finds nothing, which is
indistinguishable from green when there is nothing to find: that is the failure
NC-7 exists to exclude for DV-021 and this file is its counterpart for DV-032's
second half. So the scan is planted against — a real module, copied and given an
`UPDATE line_posterior`, must fail it — and its positive control asserts that the
scan reaches the statements the package genuinely carries.

**The one legitimate `UPDATE` is named rather than excused.** T034 sets the
active-run pointer with `UPDATE forecast_run SET is_active`, in the transaction
of its own AD-010 gives it. The predicate below allows exactly that target and
nothing else, so a second pointer statement would pass and an `UPDATE` against
any of the four artifact stores would not.

**Parsed with `ast`, never grepped.** `write.py`'s own docstrings say the word
`UPDATE` repeatedly, as does this one; a text scan would report the module that
proves the rule as the module that breaks it. Docstrings are excluded by
position and the remaining string constants are read as statements.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import model.forecast
from model.forecast.write import ACTIVATE_RUN_SQL, CLEAR_ACTIVE_RUN_SQL

#: The package under assertion, located through the module rather than by a
#: relative path from this file: the test tier and the package move
#: independently, and a `parents[3]` would keep passing while pointing at
#: nothing.
PACKAGE_ROOT = Path(model.forecast.__file__).resolve().parent

#: The verb DV-032(b) ranges over, and the head of the one statement FR-034
#: admits. A prefix rather than a whole statement: `set_active_run` and the
#: clearing statement differ after it, and both are the same permitted write.
UPDATE_VERB = "UPDATE"
PERMITTED_UPDATE_PREFIX = "UPDATE FORECAST_RUN SET IS_ACTIVE"

#: The four append-only stores FR-034 names. An `UPDATE` reaching any of them is
#: the defect; `forecast_run` is deliberately absent, because the pointer lives
#: on it and the run row is where the epic's single permitted write lands.
ARTIFACT_STORES = (
    "line_posterior",
    "held_out_prediction",
    "forecast_split_assignment",
    "forecast_diagnostic",
)

#: Statement heads recognised as SQL. Wider than the verb under assertion on
#: purpose: the positive control needs to know the scan reaches the package's
#: real statements, and a scanner that only ever looked for `UPDATE` would
#: report a clean result against a file it had failed to parse.
SQL_HEADS = frozenset(
    {"SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE", "WITH"}
)


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """Every string constant that is a docstring, by identity.

    Position rather than content: a docstring is the first statement of a
    module, class or function body and nothing else is, which is what separates
    `write.py`'s prose about `UPDATE` from `write.py`'s `UPDATE`.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def sql_statements(source: Path) -> tuple[tuple[int, str], ...]:
    """Every SQL statement one module carries, as `(line number, statement)`.

    A string constant whose first word is an SQL verb, excluding docstrings.
    Returned as data rather than asserted here so the plantings below can watch
    the predicate find what they put in front of it.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    docstrings = _docstring_nodes(tree)
    statements: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        text = node.value.strip()
        head = text.split(maxsplit=1)[0].upper() if text.split() else ""
        if head in SQL_HEADS:
            statements.append((node.lineno, text))
    return tuple(statements)


def disallowed_updates(sources: tuple[Path, ...]) -> tuple[tuple[str, int, str], ...]:
    """Every `UPDATE` in `sources` that is not the active-run pointer's.

    The predicate itself. `(module, line, statement)` so a failure names the
    place rather than reporting that one exists somewhere, and so the planted
    control can assert *which* statement was found rather than only how many.
    """
    offenders: list[tuple[str, int, str]] = []
    for source in sources:
        for lineno, statement in sql_statements(source):
            normalized = " ".join(statement.split()).upper()
            if normalized.startswith(UPDATE_VERB) and not normalized.startswith(
                PERMITTED_UPDATE_PREFIX
            ):
                offenders.append((source.name, lineno, statement))
    return tuple(offenders)


@pytest.fixture(scope="module")
def package_modules() -> tuple[Path, ...]:
    """Every module of `model.forecast`, sorted, read off the installed package."""
    return tuple(sorted(PACKAGE_ROOT.glob("*.py")))


# ---------------------------------------------------------------------------
# The positive control — the scan reaches statements that are actually there
# ---------------------------------------------------------------------------


def test_the_scan_finds_the_statements_the_package_really_carries(
    package_modules: tuple[Path, ...],
) -> None:
    """Guard: an absence over an empty scan is the failure this file exists for.

    The package's five artifact `INSERT`s and its reads are found by name, so a
    scanner that silently parsed nothing — a moved package root, a docstring
    rule that swallowed every literal — fails here rather than reporting the
    clean result it would report either way.
    """
    found = {
        source.name: [statement for _, statement in sql_statements(source)]
        for source in package_modules
    }
    every = [statement for statements in found.values() for statement in statements]

    assert len(package_modules) > 10, f"only {len(package_modules)} modules under {PACKAGE_ROOT}"
    assert sum(1 for statement in every if statement.upper().startswith("INSERT INTO")) == 5, (
        f"the scan found {sorted(found)} carrying "
        f"{[s[:40] for s in every if s.upper().startswith('INSERT')]}; transaction 1 writes "
        f"five stores, so fewer than five `INSERT`s means the scan is not reading write.py"
    )
    assert any(statement.upper().startswith("SELECT") for statement in every)
    assert {"write.py", "read.py"} <= set(found)


# ---------------------------------------------------------------------------
# The absence itself (SC-031, DV-032(b))
# ---------------------------------------------------------------------------


def test_no_module_in_the_package_updates_anything_but_the_pointer(
    package_modules: tuple[Path, ...],
) -> None:
    """DV-032(b): the writer issues no `UPDATE` against an artifact store.

    Over every module of the package rather than over the five statement
    constants `test_artifact_immutability.py` names, which is the difference
    this file adds: a statement assembled in a function body, in a module nobody
    thought to enumerate, is exactly where the next one would appear.
    """
    offenders = disallowed_updates(package_modules)

    assert offenders == (), (
        f"{offenders} issue an `UPDATE` that is not the active-run pointer's. FR-034 writes "
        f"every artifact row once and a correction is a new run rather than an edit; the "
        f"grant records that intent but cannot enforce it under a superuser connection"
    )


def test_the_permitted_updates_are_the_pointer_statements_and_only_those(
    package_modules: tuple[Path, ...],
) -> None:
    """The exception, located rather than assumed away.

    Both pointer statements are found, both live in `write.py`, and each is the
    one the writer actually exports — compared against `CLEAR_ACTIVE_RUN_SQL`
    and `ACTIVATE_RUN_SQL` rather than against a copy of their text, so a third
    `UPDATE` that happened to match the permitted prefix would still have to be
    a statement this epic declares.
    """
    updates = {
        (source.name, " ".join(statement.split()))
        for source in package_modules
        for _, statement in sql_statements(source)
        if statement.upper().startswith(UPDATE_VERB)
    }
    declared = {
        " ".join(str(statement).split()) for statement in (CLEAR_ACTIVE_RUN_SQL, ACTIVATE_RUN_SQL)
    }

    assert {name for name, _ in updates} == {"write.py"}
    assert {statement for _, statement in updates} == declared
    assert all(statement.upper().startswith(PERMITTED_UPDATE_PREFIX) for _, statement in updates)


def test_no_update_statement_names_any_of_the_four_artifact_stores(
    package_modules: tuple[Path, ...],
) -> None:
    """The same absence keyed on the object rather than on the target prefix.

    Two spellings of one rule, because either alone has a gap: a prefix check
    passes an `UPDATE forecast_run` that reached a joined artifact store, and a
    table-name check passes an `UPDATE forecast_run SET artifact_hash`. The
    permitted statements name only `forecast_run`, so both hold today.
    """
    for source in package_modules:
        for lineno, statement in sql_statements(source):
            if not statement.upper().startswith(UPDATE_VERB):
                continue
            named = [store for store in ARTIFACT_STORES if store in statement.lower()]

            assert not named, (
                f"{source.name}:{lineno} updates {named}; those four stores are append-only "
                f"within a run's write transaction (FR-034)"
            )


# ---------------------------------------------------------------------------
# NC-22 — the planted positive
# ---------------------------------------------------------------------------

#: The statement planted into a copy of a real module. An artifact store and a
#: plausible correction, because the failure FR-034 forbids is a run being
#: *repaired* in place rather than re-run — not a wild statement nobody would
#: write.
PLANTED_UPDATE = (
    "UPDATE line_posterior SET residual_tail_mass = :mass\n"
    "    WHERE run_id = :run_id AND po_line_id = :po_line_id"
)
PLANTED_CONSTANT = f'PATCH_TAIL_MASS_SQL = text(\n    """\n    {PLANTED_UPDATE}\n    """\n)\n'

#: The module the plant is made into. `write.py` deliberately: it is the module
#: that already carries the two permitted `UPDATE`s, so the control shows the
#: predicate separating a permitted statement from a forbidden one in the same
#: file rather than merely noticing that a file contains the verb.
PLANTED_MODULE = "write.py"


@pytest.fixture
def planted_module(tmp_path: Path) -> Path:
    """A real writer module with one `UPDATE` against an artifact store added.

    Copied and appended to rather than written from scratch: a control built
    from a hand-written file would test the scanner against a shape the package
    never has, and the point is that this module is otherwise exactly the one
    the absence check passes.
    """
    original = (PACKAGE_ROOT / PLANTED_MODULE).read_text(encoding="utf-8")
    target = tmp_path / PLANTED_MODULE
    target.write_text(f"{original}\n\n{PLANTED_CONSTANT}", encoding="utf-8")
    return target


def test_the_unplanted_copy_passes_the_predicate(tmp_path: Path) -> None:
    """The control's own control: the copy is clean before anything is added.

    Without it the planting below is satisfied by a predicate that rejects every
    file it is handed, which discloses as little as one that inspects nothing.
    """
    target = tmp_path / PLANTED_MODULE
    target.write_text((PACKAGE_ROOT / PLANTED_MODULE).read_text(encoding="utf-8"), encoding="utf-8")

    assert disallowed_updates((target,)) == ()
    assert sql_statements(target), "the copied module carries no SQL, so nothing was copied"


def test_a_planted_update_against_an_artifact_store_fails_the_check(
    planted_module: Path,
) -> None:
    """NC-22: the absence check finds the thing it claims nothing contains.

    The planted statement is reported by module and line, and the two permitted
    pointer statements in the same file are not — so the predicate discriminates
    rather than firing on the verb.
    """
    offenders = disallowed_updates((planted_module,))

    assert len(offenders) == 1, f"the predicate reported {offenders} for one planted statement"
    name, lineno, statement = offenders[0]
    assert name == PLANTED_MODULE
    assert "line_posterior" in statement
    assert lineno > 200, "the plant is appended, so a line number near the top is a mis-parse"


def test_the_planted_module_also_fails_the_artifact_store_clause(
    planted_module: Path,
) -> None:
    """The second spelling of the rule catches the same plant.

    Asserted separately because the two clauses fail independently: a later
    edit that widened `PERMITTED_UPDATE_PREFIX` would silence the first one, and
    this is what would still be red.
    """
    named = [
        store
        for _, statement in sql_statements(planted_module)
        if statement.upper().startswith(UPDATE_VERB)
        for store in ARTIFACT_STORES
        if store in statement.lower()
    ]

    assert named == ["line_posterior"]


def test_an_update_named_only_in_prose_is_not_reported(tmp_path: Path) -> None:
    """The false-positive direction, which decides whether the check is usable.

    `write.py`, `test_artifact_immutability.py` and this module all discuss
    `UPDATE` in prose, so a text scan would report the package as breaching
    FR-034 on every run and the check would be turned off rather than fixed.
    A docstring and a comment are planted and neither is a statement.
    """
    target = tmp_path / "prose.py"
    target.write_text(
        '"""UPDATE line_posterior SET draws = :draws — described, never issued."""\n'
        "\n"
        "# UPDATE held_out_prediction SET survival = :survival\n"
        "def documented() -> None:\n"
        '    """UPDATE forecast_diagnostic SET passed = true, in prose only."""\n',
        encoding="utf-8",
    )

    assert sql_statements(target) == ()
    assert disallowed_updates((target,)) == ()
