"""TR-002 / TR-003 / TR-004 / SC-001: the boundaries resolve independently.

Also TR-008 and TR-042 (E003): the database client and migration tooling are
declared by exactly one entry, and the root check package holds nothing that
belongs inside an entry.
"""

from __future__ import annotations

import pytest

from tests.checks.helpers.entries import (
    ALL_ENTRIES,
    PYTHON_ENTRIES,
    declared_third_party,
    first_party_sources,
    locked_distributions,
    manifest,
    normalize,
)
from tests.checks.helpers.root_checks import (
    CHECKS_ROOT,
    REPO_ROOT,
    imported_root_packages,
    root_check_modules,
)

#: E003's storage stack: the driver, the migration runner, and the Core toolkit
#: the two are driven through. Normalized names, since that is what both derived
#: sets are keyed by.
DATABASE_TOOLING = frozenset({"alembic", "psycopg", "sqlalchemy"})

#: The entry that owns the schema (ADR-0013). Everything else is asserted
#: against it rather than against a repeated literal.
SCHEMA_OWNING_ENTRY = "model"


def test_no_modeling_dependency_reaches_the_serving_resolution() -> None:
    """TR-004. Compared against *declared* third-party names.

    Shared transitives are legitimate and are not the failure this detects;
    what would matter is the modeling boundary's own stack arriving in the
    boundary that serves requests.
    """
    leaked = declared_third_party("model") & locked_distributions("api")
    assert not leaked, f"modeling distributions present in the serving resolution: {sorted(leaked)}"


def test_neither_python_boundary_declares_the_other() -> None:
    assert "model" not in {normalize(n) for n in manifest("api")["project"]["dependencies"]}
    assert "api" not in {normalize(n) for n in manifest("model")["project"]["dependencies"]}


@pytest.mark.parametrize("boundary", ["api", "model"])
def test_both_boundaries_declare_the_gateway_as_a_path_dependency(boundary: str) -> None:
    assert "gateway" in first_party_sources(boundary)


def test_gateway_carries_no_modeling_stack() -> None:
    """TR-003. The modeling stack is derived, never hand-listed."""
    intrusion = locked_distributions("gateway") & declared_third_party("model")
    assert not intrusion, f"gateway resolved set carries the modeling stack: {sorted(intrusion)}"


def test_gateway_carries_no_web_framework() -> None:
    """TR-003. Derived from what the serving boundary declares to serve HTTP."""
    web_framework = {"fastapi", "uvicorn"}
    assert web_framework <= declared_third_party("api"), "serving boundary changed; update the term"
    intrusion = locked_distributions("gateway") & web_framework
    assert not intrusion, f"gateway resolved set carries a web framework: {sorted(intrusion)}"


@pytest.mark.parametrize("entry", PYTHON_ENTRIES)
def test_first_party_names_are_excluded_from_every_derived_set(entry: str) -> None:
    """The exclusion that STF-001 and STF-002 were filed about."""
    assert not (declared_third_party(entry) & first_party_sources(entry))


def test_the_schema_owning_entry_declares_the_database_tooling() -> None:
    """TR-008. The positive half — without it the next test could pass on a typo."""
    declared = declared_third_party(SCHEMA_OWNING_ENTRY)
    missing = DATABASE_TOOLING - declared
    assert not missing, f"{SCHEMA_OWNING_ENTRY} no longer declares {sorted(missing)}"


@pytest.mark.parametrize("entry", [e for e in PYTHON_ENTRIES if e != SCHEMA_OWNING_ENTRY])
def test_no_other_entry_declares_or_resolves_the_database_tooling(entry: str) -> None:
    """TR-008. Schema assets live in one entry, so the client it needs does too.

    Checked against the resolved set as well as the declared one. A declaration
    is the intent; the lockfile is what actually arrives, and a boundary that
    pulled `sqlalchemy` in transitively would ship it without ever naming it.
    """
    intrusion = DATABASE_TOOLING & (declared_third_party(entry) | locked_distributions(entry))
    assert not intrusion, (
        f"{entry} carries {sorted(intrusion)}. Only {SCHEMA_OWNING_ENTRY} owns schema "
        f"assets (ADR-0013); the serving boundary reads published constants over the "
        f"connection rather than through the migration stack (TR-008, TR-047)."
    )


def test_no_root_check_imports_the_database_tooling() -> None:
    """TR-042: a schema test cannot live here, because its imports cannot resolve.

    The root project declares no dependencies at all, so a module here importing
    `alembic` or `psycopg` is an entry-local schema test that has been moved up
    to claim the cross-entry exception. That exception exists for checks no
    single entry owns — comparing one entry's dependency set against another's,
    or asserting on a built image — and explicitly not for this.
    """
    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): sorted(imported)
        for path in root_check_modules()
        if (imported := DATABASE_TOOLING & imported_root_packages(path))
    }
    assert not offenders, (
        f"{offenders} import the database or migration tooling from the repository root. "
        f"Entry-local schema tests belong in src/model/tests/schema/ (TR-042)."
    )


def test_no_root_check_imports_an_entry_package() -> None:
    """TR-042: cross-entry checks read entries as files, never as modules.

    Importing an entry's package is the defining move of an entry-local test —
    and it would also mean the root check ran against whichever entry's
    interpreter happened to have that package installed, which is precisely the
    ambiguity a four-entry layout exists to remove.
    """
    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): sorted(imported)
        for path in root_check_modules()
        if (imported := set(ALL_ENTRIES) & imported_root_packages(path))
    }
    assert not offenders, (
        f"{offenders} import an entry package. A check at the repository root must reach "
        f"an entry through its files — manifest, lockfile, source tree, image — never "
        f"through an import (TR-042)."
    )


def test_the_import_scan_covers_the_root_check_package() -> None:
    """Guard: the two scans above assert nothing if they find no modules."""
    modules = root_check_modules()
    assert len(modules) > 5, f"only {len(modules)} modules found under {CHECKS_ROOT}"
    assert any(path.name == "test_dependency_isolation.py" for path in modules)
