"""TR-002 / TR-003 / TR-004 / SC-001: the boundaries resolve independently.

Also TR-008 and TR-042 (E003): the database client and migration tooling are
declared by exactly one entry, and the root check package holds nothing that
belongs inside an entry. And, from E007, DV-022 / SC-021's first conjunct: no
request-time entry point reaches the fit.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

from tests.checks.helpers.entries import (
    ALL_ENTRIES,
    PYTHON_ENTRIES,
    SRC_ROOT,
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

#: The half of that stack which is genuinely exclusive to the schema owner
#: ({SAD:ADR-0016}, correcting the clause in {SAD:ADR-0013}'s consequences).
#:
#: Correction of 2026-07-26, recorded rather than applied silently. This set was
#: originally the whole of `DATABASE_TOOLING`, which made E003's own design
#: unbuildable: ADR-0013's chosen option has `/src/api` reading `schema_constants`
#: **over a connection**, and lists "`/src/api` pays a startup read against the
#: database before it can serve" among its costs — neither is possible without a
#: driver. The assertion below even said so in its own failure message while
#: forbidding the thing that message describes.
#:
#: What is actually exclusive is the migration and ORM stack, because that is
#: what carries schema *authorship*. A driver is how any entry talks to Postgres
#: at all, and ADR-0016 sanctions one wherever an accepted record already grants
#: the purpose — `/src/api` for the constants read and SQL-side risk computation
#: ({SAD:ADR-0004}), `/src/gateway` for writing invocation records
#: ({SAD:ADR-0010}). Narrowed, not relaxed: no object was added, and the
#: schema-asset check below closes the gap a name-based set left open anyway.
MIGRATION_STACK = frozenset({"alembic", "sqlalchemy"})

#: Filenames and directories that constitute schema authorship. Checked as
#: files rather than as distribution names, because an entry that hand-rolls
#: DDL never imports anything a name-based set would catch.
SCHEMA_ASSET_NAMES = ("alembic.ini", "versions", "env.py", "script.py.mako")

#: Distributions the schema owner declares that are nonetheless shared
#: infrastructure rather than part of the modeling stack.
#:
#: The two checks below derive "the modeling stack" from what `model` declares,
#: which is the right instinct — a hand-listed stack goes stale the moment
#: someone adds a dependency. But the derivation is only as good as its
#: exclusions, and the driver is declared by `model` while belonging to no
#: boundary in particular: {SAD:ADR-0016} sanctions it for `/src/api` and
#: `/src/gateway` too. Without this subtraction those checks read every shared
#: transitive as a modeling intrusion, which is precisely what the first one's
#: own docstring says it is not looking for.
#:
#: `numpy` joins on the same ground, from E008 ({SAD:ADR-0023}) — and this copy
#: must move with the one in `helpers/image_contents.py`, never alone. The
#: serving image is required to carry a local-inference runtime (ADR-0006's
#: cross-encoder session, ADR-0019's request-time query embedding) and
#: `onnxruntime` pulls NumPy transitively.
#:
#: The width is **NumPy alone**. `onnxruntime` and `tokenizers` are deliberately
#: absent: E008 relocated both to `/src/gateway`, so they leave the derived set
#: on their own, and naming them here would trip
#: `test_every_excluded_name_is_still_declared_by_the_schema_owner` below.
#:
#: What TR-003 and TR-004 protect narrows accordingly, and the cost is stated
#: rather than glossed: PyMC, ArviZ and pandas are still derived and never
#: listed, but NumPy leaves the set the image is *guaranteed* to exclude, so it
#: could later arrive by a route nobody intended with nothing reporting it.
SHARED_INFRASTRUCTURE = frozenset({"psycopg", "numpy"})

#: The entry that owns the schema (ADR-0013). Everything else is asserted
#: against it rather than against a repeated literal.
SCHEMA_OWNING_ENTRY = "model"


def test_no_modeling_dependency_reaches_the_serving_resolution() -> None:
    """TR-004. Compared against *declared* third-party names.

    Shared transitives are legitimate and are not the failure this detects;
    what would matter is the modeling boundary's own stack arriving in the
    boundary that serves requests.
    """
    modeling_stack = declared_third_party("model") - SHARED_INFRASTRUCTURE
    leaked = modeling_stack & locked_distributions("api")
    assert not leaked, f"modeling distributions present in the serving resolution: {sorted(leaked)}"


def test_neither_python_boundary_declares_the_other() -> None:
    assert "model" not in {normalize(n) for n in manifest("api")["project"]["dependencies"]}
    assert "api" not in {normalize(n) for n in manifest("model")["project"]["dependencies"]}


@pytest.mark.parametrize("boundary", ["api", "model"])
def test_both_boundaries_declare_the_gateway_as_a_path_dependency(boundary: str) -> None:
    assert "gateway" in first_party_sources(boundary)


def test_gateway_carries_no_modeling_stack() -> None:
    """TR-003. The modeling stack is derived, never hand-listed."""
    modeling_stack = declared_third_party("model") - SHARED_INFRASTRUCTURE
    intrusion = locked_distributions("gateway") & modeling_stack
    assert not intrusion, f"gateway resolved set carries the modeling stack: {sorted(intrusion)}"


def test_the_shared_infrastructure_exclusion_cannot_hide_the_modeling_stack() -> None:
    """Guard on the subtraction above, because an exclusion list is a loophole.

    Two ways `SHARED_INFRASTRUCTURE` could rot. Someone adds `pymc` to it and
    the two checks that depend on it pass while the serving image grows a
    modeling stack — so the heavy packages are asserted absent from it by name.
    Or it drifts to cover something the schema owner no longer declares, leaving
    a dead entry that quietly weakens nothing but tells the next reader a
    falsehood — so every member is required to still be declared by `model`.
    """
    # NumPy left this set at E008 ({SAD:ADR-0023}) because the serving image is
    # required to carry the inference runtime that pulls it. The three that
    # remain are what actually make the image fat, and none of them is excluded.
    heavy = {"pymc", "arviz", "pandas"}
    smuggled = heavy & SHARED_INFRASTRUCTURE
    assert not smuggled, (
        f"{sorted(smuggled)} is excluded from the derived modeling stack. That defeats "
        f"TR-003 and TR-004: the serving image could acquire it and both checks would "
        f"still pass. The exclusion is for shared infrastructure, not for the stack."
    )

    declared = declared_third_party(SCHEMA_OWNING_ENTRY)
    stale = SHARED_INFRASTRUCTURE - declared
    assert not stale, (
        f"{sorted(stale)} is excluded but {SCHEMA_OWNING_ENTRY} no longer declares it, so "
        f"the subtraction removes nothing and the comment explaining it is wrong."
    )

    assert heavy <= declared, (
        f"{SCHEMA_OWNING_ENTRY} no longer declares {sorted(heavy - declared)}; the guard "
        f"above compares against a stack that has moved, so update the term."
    )


def test_gateway_carries_no_web_framework() -> None:
    """TR-003. Derived from what the serving boundary declares to serve HTTP."""
    web_framework = {"fastapi", "uvicorn"}
    assert web_framework <= declared_third_party("api"), "serving boundary changed; update the term"
    intrusion = locked_distributions("gateway") & web_framework
    assert not intrusion, f"gateway resolved set carries a web framework: {sorted(intrusion)}"


# --- E004 TR-029 / TR-075: what the gateway's resolution must not contain ----

#: The OpenTelemetry family, matched by prefix rather than enumerated.
#:
#: A fixed list would name the four or five distributions that exist today and
#: pass on the sixth. The family is open by construction — every instrumentation
#: and exporter package is published under this prefix — so the prefix is the
#: honest denominator.
#:
#: `opentelemetry-semantic-conventions` is deliberately **not** carved out.
#: TR-075 permits borrowing the generative-AI convention's field *names*, which
#: costs nothing and installs nothing; taking the package that defines them as a
#: dependency is the beginning of a pipeline, which is the thing excluded.
OPENTELEMETRY_PREFIX = "opentelemetry"


def _telemetry_members(distributions: set[str]) -> set[str]:
    return {name for name in distributions if name.startswith(OPENTELEMETRY_PREFIX)}


def test_gateway_carries_no_opentelemetry_sdk() -> None:
    """TR-075: the invocation record is the only telemetry this epic emits.

    Asserted against the resolved set rather than the manifest, which is what
    makes it cover TR-029's "or any extra" — an extra's packages appear in the
    lockfile whether or not the default resolution installs them, so an SDK
    added under `provider` would be caught here and invisible to a scan of the
    base requirements.

    The reason this is a dependency check and not a code review note: a gateway
    that took the SDK would emit spans by configuration rather than by code,
    and no assertion over `/src` would see it.
    """
    intrusion = _telemetry_members(locked_distributions("gateway"))
    assert not intrusion, (
        f"the gateway resolved set carries {sorted(intrusion)}. TR-075 makes the "
        f"invocation record the only telemetry this epic emits: no spans, no "
        f"metrics, no exporter, no propagator. The generative-AI convention "
        f"supplies field names here, not a pipeline."
    )


@pytest.mark.parametrize("entry", PYTHON_ENTRIES)
def test_no_python_entry_carries_an_opentelemetry_sdk(entry: str) -> None:
    """The same exclusion across the repository, and it is not redundant.

    The gateway declares no telemetry, but both boundaries declare the gateway
    as a path dependency. An SDK arriving in `api` or `model` would sit one
    import away from the module writing invocation records, and TR-075's claim
    would become true of one entry and false of the process running it.
    """
    intrusion = _telemetry_members(locked_distributions(entry))
    assert not intrusion, f"{entry} resolved set carries {sorted(intrusion)} (TR-075)"


def test_the_telemetry_matcher_reports_a_planted_member() -> None:
    """A prefix match over a set that never contains one passes forever.

    Both the exporter and the SDK are planted, because the prefix is the entire
    mechanism: if it were ever narrowed to an exact name, one of these would
    survive and the checks above would keep reporting clean.
    """
    planted = {"opentelemetry-sdk", "opentelemetry-exporter-otlp", "pydantic", "psycopg"}
    assert _telemetry_members(planted) == {
        "opentelemetry-sdk",
        "opentelemetry-exporter-otlp",
    }


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
def test_no_other_entry_declares_or_resolves_the_migration_stack(entry: str) -> None:
    """TR-008. Schema *authorship* lives in one entry, so its tooling does too.

    Checked against the resolved set as well as the declared one. A declaration
    is the intent; the lockfile is what actually arrives, and a boundary that
    pulled `sqlalchemy` in transitively would ship it without ever naming it.

    Scoped to `MIGRATION_STACK` rather than the whole storage stack: a driver is
    not schema authorship, and forbidding it here would forbid the constants
    read this requirement exists to describe. See the note on `MIGRATION_STACK`.
    """
    intrusion = MIGRATION_STACK & (declared_third_party(entry) | locked_distributions(entry))
    assert not intrusion, (
        f"{entry} carries {sorted(intrusion)}. Only {SCHEMA_OWNING_ENTRY} authors schema "
        f"assets (ADR-0013, ADR-0016); another entry carrying the migration stack can "
        f"declare DDL, which is the decision that belongs to one plan (TR-008, TR-047)."
    )


@pytest.mark.parametrize("entry", [e for e in PYTHON_ENTRIES if e != SCHEMA_OWNING_ENTRY])
def test_no_other_entry_holds_schema_assets(entry: str) -> None:
    """TR-008 / TR-047. The boundary that matters, asserted over files.

    Stronger than the distribution-name check above and the reason narrowing it
    costs nothing: an entry could author DDL in raw SQL and import nothing at
    all. What is forbidden is *holding the assets* — an Alembic config, a
    `versions/` directory, a migration environment — not owning a driver.
    """
    entry_root = SRC_ROOT / entry
    found = sorted(
        path.relative_to(entry_root).as_posix()
        for name in SCHEMA_ASSET_NAMES
        for path in entry_root.rglob(name)
        if ".venv" not in path.parts
    )
    assert not found, (
        f"{entry} holds schema assets {found}. Only {SCHEMA_OWNING_ENTRY} authors schema "
        f"(ADR-0013); an entry outside it declaring DDL takes a decision another epic's "
        f"plan owns (TR-008, TR-047)."
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


# --- E007 DV-022 / SC-021: no request-time entry point reaches the fit --------
#
# The modeling entry's own `import-linter` contract covers the other direction —
# `model.forecast` reaching `model.llm` or `gateway` — and its failing direction
# is evidenced by the `forecast_offline` fixture and by NC-15 inside that entry.
# This is the conjunct no entry owns: the serving boundary must have **no import
# path to the fit at all**, over the transitive graph rather than over its direct
# imports, and comparing one entry's source against another's is exactly the
# cross-entry verification `/tests` exists for.
#
# The closure is walked rather than assumed. `/src/api` declares `gateway` as a
# path dependency, so a module in the gateway's shipped package is one import
# away from every request the serving boundary handles, and a scan of `/src/api`
# alone would miss a reach laundered through it.

#: The entry that serves requests, and the package name the fit lives under.
#: FR-025's subject and object, named once.
SERVING_ENTRY = "api"
MODELING_PACKAGE = "model"
FIT_MODULE = "model.forecast"

#: Directories excluded wherever they appear beneath an entry's source tree.
SKIPPED_DIRS = frozenset({".venv", "__pycache__", ".ruff_cache", ".pytest_cache"})


def _package_sources(entry: str) -> list[Path]:
    """Every Python module in one entry's **shipped** package tree.

    `src/<entry>/src` and not the whole entry directory: an entry's own tests are
    not a request-time entry point, and including them would make this check fail
    the moment a serving test wanted to read a modeling artifact off disk. What
    ships is what a request can reach.
    """
    root = SRC_ROOT / entry / "src"
    return sorted(
        path for path in root.rglob("*.py") if not any(part in SKIPPED_DIRS for part in path.parts)
    )


def request_time_closure(entry: str = SERVING_ENTRY) -> dict[str, list[Path]]:
    """The first-party packages a request can reach, walked transitively.

    Starts at the serving entry and follows every first-party path dependency it
    declares, then theirs, so the result is the closure rather than one layer of
    it. Keyed by entry so a failure names which package holds the offending
    module rather than reporting a path and leaving the reader to work out whose
    it is.
    """
    closure: dict[str, list[Path]] = {}
    pending = [entry]
    while pending:
        current = pending.pop()
        if current in closure:
            continue
        closure[current] = _package_sources(current)
        pending.extend(name for name in first_party_sources(current) if name in PYTHON_ENTRIES)
    return closure


def modules_importing(package: str, closure: dict[str, list[Path]]) -> dict[str, list[str]]:
    """Every module in the closure whose imports reach `package`, by entry.

    Parsed rather than grepped, following `imported_root_packages`: these
    boundaries are named constantly in docstrings and failure messages, and a
    scan that counted those would be unusable. Returned as data so the planted
    control below can observe the predicate find something.
    """
    return {
        entry: found
        for entry, paths in closure.items()
        if (found := [_named(path) for path in paths if package in imported_root_packages(path)])
    }


def _named(path: Path) -> str:
    """A module's path as a reader would cite it, repository-relative where it is.

    The fallback is not decoration: the planted control below writes outside the
    checkout, and a failure message is worth nothing if producing it raises.
    """
    return (
        path.relative_to(REPO_ROOT).as_posix()
        if path.is_relative_to(REPO_ROOT)
        else path.as_posix()
    )


def test_no_request_time_entry_point_reaches_the_fit() -> None:
    """DV-022 / SC-021, first conjunct: FR-025's "no request-time fitting".

    Over the transitive first-party closure, not over `/src/api` alone. The
    gateway ships inside every serving process, so a module there importing the
    modeling package would put the fit one import away from a request while
    leaving `/src/api` looking clean.
    """
    closure = request_time_closure()
    offenders = modules_importing(MODELING_PACKAGE, closure)

    assert not offenders, (
        f"{offenders} import {MODELING_PACKAGE!r} from inside the request-time closure "
        f"{sorted(closure)}. Fitting is an offline job invoked as a console entry point "
        f"({{SAD:ADR-0011}}); no request-time entry point may reach it (FR-025, DV-022, "
        f"SC-021), and {FIT_MODULE!r} lives under that package."
    )


def test_the_serving_resolution_could_not_import_the_fit_even_dynamically() -> None:
    """The half a source scan does not reach, closed by the resolution instead.

    G-17 records the residual honestly: a static scan reads import statements and
    cannot see a module name assembled at runtime. It does not have to here,
    because the modeling package is not in the serving entry's resolved set at
    all — there is nothing present to import, however the name were constructed.
    """
    resolved = locked_distributions(SERVING_ENTRY)
    declared = declared_third_party(SERVING_ENTRY) | first_party_sources(SERVING_ENTRY)

    assert MODELING_PACKAGE not in resolved
    assert MODELING_PACKAGE not in declared


def test_the_closure_is_walked_rather_than_assumed() -> None:
    """Guard: the scan above asserts nothing if it found no modules to scan.

    Both properties matter. The closure must reach past the serving entry — the
    gateway is the layer a laundered reach would live in — and each member must
    actually contain modules, since an empty list satisfies every "no offender"
    assertion ever written.
    """
    closure = request_time_closure()

    assert SERVING_ENTRY in closure
    assert "gateway" in closure, (
        f"the request-time closure is {sorted(closure)} and does not include the gateway, "
        f"which {SERVING_ENTRY!r} declares as a path dependency; a scan that stopped at one "
        f"entry would miss a reach laundered through the package that ships beside it"
    )
    assert all(paths for paths in closure.values()), (
        f"an entry in the closure contributed no modules: "
        f"{ {entry: len(paths) for entry, paths in closure.items()} }"
    )


def test_the_reach_detector_reports_a_planted_import(tmp_path: Path) -> None:
    """The predicate finds what it is looking for, planted rather than argued.

    Both spellings, because they parse to different nodes and an implementation
    reading only `ast.Import` would pass the repository while missing the form a
    serving module would actually be written in.
    """
    direct = tmp_path / "direct.py"
    direct.write_text("import model.forecast.fit\n", encoding="utf-8")
    indirect = tmp_path / "indirect.py"
    indirect.write_text("from model.forecast import fit\n", encoding="utf-8")
    innocent = tmp_path / "innocent.py"
    innocent.write_text('"""Mentions model.forecast in prose only."""\n', encoding="utf-8")

    found = modules_importing(MODELING_PACKAGE, {"planted": [direct, indirect, innocent]})

    assert set(found) == {"planted"}
    assert len(found["planted"]) == 2
    assert not any("innocent" in name for name in found["planted"])


# --- E007 T112: the fit ships as an entry point and declares no new dependency -
#
# DV-022's other side. The check above says the serving boundary cannot reach the
# fit; this says how the fit *is* reached — two console entry points on the
# modeling entry, per {SAD:ADR-0011} — and that adding it cost the entry no new
# declared dependency, which is what keeps the two Python entries' resolved sets
# as far apart as they were before E007 existed.
#
# Both halves are manifest and lockfile facts, so they belong to a root check
# rather than to the modeling entry: TR-042 forbids importing an entry package
# here, and nothing below does. The package's imports are read with `ast` off
# disk, exactly as the reach detector above reads the serving closure.

#: The entry that owns the fit, and the shipped package the two entry points
#: resolve into.
MODELING_ENTRY = "model"
FIT_PACKAGE_PARTS = ("src", "model", "forecast")

#: E007's two console entry points, each mapped to the module attribute it
#: resolves to. Two rather than one with a mode flag, so a workflow step names
#: the job it runs.
FORECAST_ENTRY_POINTS: dict[str, str] = {
    "forecast-fit": "model.forecast.fit:main",
    "forecast-reproduce": "model.forecast.reproduce:main",
}

#: Every third-party distribution the modeling entry declares, and the epic each
#: arrived with. **This is the no-new-dependency claim**: E007 appears nowhere in
#: it, which is only assertable against an enumeration somebody has to edit. A
#: distribution added by a later epic fails here rather than arriving unremarked
#: in the entry that must stay resolvable apart from the serving one.
#:
#: **E006's four were added on 2026-07-28, at the merge.** This enumeration was
#: authored on E007's branch while E006 was declaring `onnxruntime`,
#: `tokenizers`, `pysbd` and `pgvector` on its own — two branches from one
#: baseline, and git merged the manifest and the enumeration without a conflict
#: because neither side touched the other's lines. The check did its job: it
#: went red on the merge naming exactly the four, which is what an equality
#: against a hand-maintained list is for. They are E006's ingestion-runtime
#: dependencies, reviewed in that epic's plan, and none of them is reachable
#: from `model.forecast`; E007's no-new-dependency claim is unaffected.
DECLARED_BY_THE_MODELING_ENTRY: dict[str, str] = {
    "alembic": "E003 — the migration runner",
    "arviz": "the modeling stack, declared before E007 — sampler diagnostics",
    "jsonschema": "E002 — corpus manifest validation",
    "numpy": "the scaffold — arrays throughout",
    "pandas": "the modeling stack, declared before E007 — the summary frame",
    "pdfplumber": "E002 — corpus extraction",
    "pgvector": "E006 — the psycopg 3 adapter for bulk-loading embeddings",
    "pillow": "E002 — corpus rendering",
    "psycopg": "E003 — the driver",
    "pymc": "the modeling stack, declared before E007 — the sampler",
    "pysbd": "E006 — the pinned terminal sentence split below a paragraph",
    "reportlab": "E002 — corpus generation",
    "sqlalchemy": "E003 — the Core toolkit the driver is used through",
    # `onnxruntime` and `tokenizers` were here until E008 relocated both to
    # /src/gateway ({SAD:ADR-0023}). This is an *equality* assertion, not a
    # subset one, so leaving them would fail on symmetric difference the moment
    # the manifest dropped them — the entry no longer declares them and reaches
    # them through the gateway instead.
}

#: Imports `model.forecast` makes that the entry does not declare, with the
#: declared distribution each reaches it through. Neither is a new dependency and
#: neither may become one silently: the mapping is checked against the entry's
#: own lockfile below, so a name here that is *not* a requirement of a declared
#: distribution fails rather than being taken on trust.
REACHED_THROUGH: dict[str, str] = {
    "pytensor": "pymc",
}

#: Named only inside a `TYPE_CHECKING` guard, and therefore not a dependency at
#: all: `sample.py` wants `xarray.DataTree` as an annotation and never a value,
#: because ArviZ 1.x retired `InferenceData` and moved the role there. Asserted
#: as guarded rather than allowed, so a later runtime import of it is a change
#: this check reports.
TYPE_CHECKING_ONLY = ("xarray",)


def _fit_package_modules() -> list[Path]:
    """Every module of the shipped fit package, read off disk and never imported."""
    root = SRC_ROOT.joinpath(MODELING_ENTRY, *FIT_PACKAGE_PARTS)
    return sorted(
        path for path in root.rglob("*.py") if not any(part in SKIPPED_DIRS for part in path.parts)
    )


def _guarded_nodes(tree: ast.Module) -> set[int]:
    """Every node sitting inside an `if TYPE_CHECKING:` body, by identity."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        named = isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
        attributed = isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        if named or attributed:
            guarded.update(id(child) for child in ast.walk(node))
    return guarded


def package_imports(*, runtime_only: bool) -> dict[str, set[str]]:
    """The fit package's imported root packages, keyed by module name.

    `runtime_only` drops everything under a `TYPE_CHECKING` guard, which is the
    distinction the no-new-dependency claim turns on: a guarded import names a
    type and asserts no dependency, and treating the two alike would either
    report `xarray` as undeclared or let a real import hide behind a guard.
    """
    found: dict[str, set[str]] = {}
    for path in _fit_package_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        guarded = _guarded_nodes(tree) if runtime_only else set()
        roots: set[str] = set()
        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".", 1)[0])
        found[path.name] = roots
    return found


def locked_requirements(entry: str) -> dict[str, set[str]]:
    """Each locked distribution's own direct requirements, from the entry's lockfile.

    Read rather than assumed, so "reached through PyMC" is a fact about the
    resolution this entry actually pins instead of a claim in a comment.
    """
    lock = tomllib.loads((SRC_ROOT / entry / "uv.lock").read_text(encoding="utf-8"))
    return {
        normalize(package["name"]): {
            normalize(requirement["name"]) for requirement in package.get("dependencies", [])
        }
        for package in lock["package"]
    }


def test_the_fit_is_reached_as_a_console_entry_point_on_the_modeling_entry() -> None:
    """{SAD:ADR-0011}: two entry points, each resolving into the fit package.

    The complement of the reach check above — the fit is unreachable from a
    request *and* reachable as a job — because "no import path" on its own is
    equally satisfied by a package nothing can run at all.
    """
    scripts = manifest(MODELING_ENTRY)["project"]["scripts"]
    serving = manifest(SERVING_ENTRY).get("project", {}).get("scripts", {})

    for name, target in FORECAST_ENTRY_POINTS.items():
        assert scripts.get(name) == target, (
            f"`src/{MODELING_ENTRY}/pyproject.toml` declares {name!r} as {scripts.get(name)!r} "
            f"rather than {target!r}; the fit is invoked through the modeling entry's own "
            f"environment and a workflow step names the job it runs"
        )
    assert not set(FORECAST_ENTRY_POINTS) & set(serving), (
        f"{SERVING_ENTRY!r} declares a forecast entry point, which would put the fit inside "
        f"the request-serving distribution however its imports are arranged"
    )


def test_the_modeling_entry_declares_exactly_the_reviewed_dependency_set() -> None:
    """T112's claim: `model.forecast` added no declared dependency.

    An equality against an enumeration rather than a subset check, because the
    claim is that nothing was *added* — and every subset assertion ever written
    is satisfied by a manifest that grew.
    """
    declared = declared_third_party(MODELING_ENTRY)

    assert declared == set(DECLARED_BY_THE_MODELING_ENTRY), (
        f"the modeling entry now declares "
        f"{sorted(declared ^ set(DECLARED_BY_THE_MODELING_ENTRY))} on one side only. Each "
        f"entry keeps an independent manifest so serving/modeling isolation is mechanically "
        f"assertable; a distribution arriving here is a decision, not a detail"
    )
    assert all(reason.strip() for reason in DECLARED_BY_THE_MODELING_ENTRY.values())


def test_every_runtime_import_the_fit_makes_is_declared_or_reached_through_one() -> None:
    """Nothing the fit imports at runtime is undeclared *and* unaccounted for.

    Two admissible answers and no third: the entry declares it, or it is a
    requirement of a declared distribution and named in `REACHED_THROUGH` with
    that distribution. The second is checked against the lockfile, so the
    accounting cannot be a comment that has stopped being true.
    """
    requirements = locked_requirements(MODELING_ENTRY)
    declared = declared_third_party(MODELING_ENTRY)
    first_party = {MODELING_PACKAGE, *first_party_sources(MODELING_ENTRY)}
    unaccounted: dict[str, set[str]] = {}
    for module, roots in package_imports(runtime_only=True).items():
        outside = {
            normalize(root)
            for root in roots
            if normalize(root) not in declared
            and normalize(root) not in first_party
            and root not in sys.stdlib_module_names
        }
        if surprising := outside - set(REACHED_THROUGH):
            unaccounted[module] = surprising

    assert not unaccounted, (
        f"{unaccounted} are imported by the fit at runtime, declared by neither the entry nor "
        f"`REACHED_THROUGH`. E007 declares no new dependency, so an import outside both sets "
        f"is one arriving without the decision being taken"
    )
    for name, through in REACHED_THROUGH.items():
        assert normalize(through) in declared
        assert normalize(name) in requirements[normalize(through)], (
            f"{name!r} is recorded as reaching this entry through {through!r}, and "
            f"`src/{MODELING_ENTRY}/uv.lock` does not list it among that distribution's "
            f"requirements"
        )


def test_the_type_checking_only_imports_are_named_and_never_reached_at_runtime() -> None:
    """`xarray` is an annotation, not a dependency, and the guard is what says so.

    Both directions: it is imported somewhere under a guard — so the claim is
    about a real import rather than about a name nothing uses — and it appears
    in no module's runtime set.
    """
    runtime = package_imports(runtime_only=True)
    every = package_imports(runtime_only=False)

    for name in TYPE_CHECKING_ONLY:
        assert any(name in roots for roots in every.values()), (
            f"{name!r} is recorded as a guarded import and no module imports it at all"
        )
        assert not any(name in roots for roots in runtime.values()), (
            f"{name!r} is imported at runtime by "
            f"{[module for module, roots in runtime.items() if name in roots]}; it is declared "
            f"nowhere, so naming it outside a `TYPE_CHECKING` guard asserts a dependency the "
            f"manifest does not"
        )
        assert normalize(name) not in declared_third_party(MODELING_ENTRY)


def test_the_fit_package_import_scan_reaches_something() -> None:
    """Guard: every assertion above is vacuous over a package that read as empty."""
    modules = package_imports(runtime_only=False)

    assert len(modules) > 15, f"only {len(modules)} modules under the fit package"
    assert {"fit.py", "write.py"} <= set(modules)
    assert any("pymc" in roots for roots in modules.values()), (
        "no module imports the sampler, so the scan is reading something other than the fit"
    )
