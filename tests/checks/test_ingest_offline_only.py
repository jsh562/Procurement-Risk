"""FR-044 (T080): no ingestion module is reachable from a request-serving entry.

"Ingestion runs offline only, never on a request path" is a **structural** claim
or it is nothing. A comment saying the job is offline survives the day someone
imports `model.ingest.writer` from a FastAPI router to reuse one helper, and the
serving image then carries the chunker, the ONNX session and the corpus reader —
along with a code path that can open a per-document transaction while answering
a request.

So the assertion here is a **reachability** assertion over imports rather than a
scan for a forbidden name in the obvious place. Two halves, and neither alone is
enough:

1. **Transitive.** Every module under `/src/api` is a request-serving module, and
   the closure of what they import — through every intermediate module inside
   the entry — must contain nothing from the modeling entry. A direct-import
   check is satisfied by one more hop, which is the shape the evasion takes.
2. **Structural.** `model` is not in `/src/api`'s declared dependencies and not
   in its resolved lockfile, so the import could not resolve even if someone
   wrote it. That is what makes the first half hold for imports nobody has
   written yet.

**The mechanism is evidenced rather than assumed.** `/src/api` is small today,
so a reachability check over it passes whether or not the traversal works at
all. `test_a_laundered_import_is_reported` plants a two-hop path in a scratch
tree and requires the same function to report it — a check that would pass
against an empty implementation is not a check.

Where this lives follows the rule the rest of this directory does: it asserts a
boundary **between two entries**, so no single entry owns it, which is the narrow
`/tests` exception `test_layout.py` and `test_model_facing_placement.py` already
sit under.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

from tests.checks.helpers.entries import (
    SRC_ROOT,
    declared_third_party,
    locked_distributions,
    manifest,
    normalize,
)

#: The request-serving entry. One entry, named rather than derived: `/src/web`
#: is TypeScript and imports no Python at all, and `/src/gateway` is a shared
#: library rather than an entry point that answers requests.
SERVING_ENTRY = "api"

#: The modeling entry's root package, and the sub-packages this epic adds to it.
#: The whole package is forbidden and the three are named for the failure
#: message: "no ingestion module" is what FR-044 says, and a reader who has just
#: broken the rule wants to know which one they reached.
MODELING_PACKAGE = "model"
INGESTION_PACKAGES = ("model.ingest", "model.llm", "model.compute")

EXCLUDED_DIRS = frozenset({"__pycache__", ".venv", ".ruff_cache", ".pytest_cache"})


def _python_files(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if path.is_file() and not any(part in EXCLUDED_DIRS for part in path.parts)
    ]


def _module_name(path: Path, package_root: Path) -> str:
    """The dotted module name of `path` within the package rooted at its parent."""
    relative = path.relative_to(package_root.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(source: str, module: str) -> set[str]:
    """Every module name this source binds, absolute and relative alike.

    All four syntactic forms are the same edge and none is more honest than the
    others: `import model.ingest.writer`, `import model.ingest as m`,
    `from model.ingest import writer`, `from . import writer`. A traversal that
    recognised only the first would be defeated by rewriting the statement,
    which is not a defence.

    A relative import is resolved against `module`'s own package, because a
    two-hop path inside the serving entry is exactly how a forbidden import gets
    laundered — the router imports a local helper and the helper reaches across.
    """
    found: set[str] = set()
    package = module.rsplit(".", 1)[0] if "." in module else module
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                prefix = f"{base}.{node.module}" if node.module else base
            else:
                prefix = node.module or ""
            if prefix:
                found.add(prefix)
                found.update(f"{prefix}.{alias.name}" for alias in node.names)
    return found


def import_graph(package_root: Path) -> Mapping[str, frozenset[str]]:
    """Every module in the package, mapped to the module names it imports."""
    return {
        _module_name(path, package_root): frozenset(
            _imported_modules(path.read_text(encoding="utf-8"), _module_name(path, package_root))
        )
        for path in _python_files(package_root)
    }


def reachable_modules(graph: Mapping[str, frozenset[str]], roots: Iterable[str]) -> set[str]:
    """The transitive closure of `roots` under `graph`.

    Names outside the graph — third-party distributions, the standard library,
    another entry's package — are included in the result and not followed. That
    is the point: reaching `model.ingest.writer` is the violation, and whether
    its own imports are visible from here is irrelevant to whether it was
    reached.
    """
    seen: set[str] = set()
    pending = list(roots)
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        pending.extend(graph.get(module, frozenset()))
    return seen


def forbidden_reach(reached: Iterable[str]) -> list[str]:
    """Every reached name inside the modeling entry, sorted."""
    return sorted(
        name
        for name in reached
        if name == MODELING_PACKAGE or name.startswith(f"{MODELING_PACKAGE}.")
    )


@pytest.fixture(scope="module")
def serving_graph() -> Mapping[str, frozenset[str]]:
    root = SRC_ROOT / SERVING_ENTRY / "src" / SERVING_ENTRY
    assert root.is_dir(), f"the request-serving entry is not at {root}"
    return import_graph(root)


def test_no_ingestion_module_is_reachable_from_a_request_serving_entry(
    serving_graph: Mapping[str, frozenset[str]],
) -> None:
    """FR-044, transitively. Every serving module is a root of the traversal.

    Every module under `/src/api` is treated as an entry point rather than only
    the ones a router names: an ASGI application is assembled from whatever it
    imports, and a module that is currently unreferenced is one import away from
    being on the request path.
    """
    assert serving_graph, "no module was found under the request-serving entry"
    reached = reachable_modules(serving_graph, serving_graph)
    offenders = forbidden_reach(reached)
    assert not offenders, (
        f"FR-044: the request-serving entry reaches {offenders}. Ingestion runs offline "
        f"only and never on a request path — {list(INGESTION_PACKAGES)} carry the chunker, "
        f"the ONNX session, the corpus reader and a per-document write transaction, none of "
        f"which may be reachable while answering a request."
    )


def test_a_laundered_import_is_reported(tmp_path: Path) -> None:
    """The traversal is evidenced, not assumed (the negative control).

    `/src/api` holds three modules today, so the assertion above passes whether
    or not the closure is computed. Here a two-hop path — a router importing a
    local helper which imports `model.ingest.writer` — is planted in a scratch
    tree and the same functions must report it. A direct-import check would call
    the helper's import invisible, which is precisely the shape a laundered
    dependency takes.
    """
    package = tmp_path / "src" / "seeded" / "src" / "seeded"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "routes.py").write_text(
        "from seeded import helpers\n\n\ndef handler():\n    return helpers.answer()\n",
        encoding="utf-8",
    )
    (package / "helpers.py").write_text(
        "from model.ingest.writer import write_document_generation\n\n\n"
        "def answer():\n    return write_document_generation\n",
        encoding="utf-8",
    )

    graph = import_graph(package)
    assert set(graph) == {"seeded", "seeded.routes", "seeded.helpers"}

    direct = forbidden_reach(graph["seeded.routes"])
    assert not direct, "the seeded router imports nothing forbidden directly — that is the point"

    reached = forbidden_reach(reachable_modules(graph, ["seeded.routes"]))
    assert "model.ingest.writer" in reached, (
        "the traversal did not follow `seeded.routes` -> `seeded.helpers` -> "
        "`model.ingest.writer`, so it would not have found a laundered import in the real "
        "serving entry either"
    )


def test_a_relative_import_is_followed_too(tmp_path: Path) -> None:
    """`from . import helpers` is the same edge and is resolved as one.

    Held separately from the two-hop control because it is a different way for
    the traversal to be wrong: a graph keyed on absolute names alone records the
    relative form as an import of nothing and reports a clean closure.
    """
    package = tmp_path / "seeded"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "routes.py").write_text("from . import helpers\n", encoding="utf-8")
    (package / "helpers.py").write_text("import model.compute.metrics\n", encoding="utf-8")

    graph = import_graph(package)
    reached = forbidden_reach(reachable_modules(graph, ["seeded.routes"]))
    assert "model.compute.metrics" in reached


def test_the_serving_entry_cannot_resolve_the_modeling_entry() -> None:
    """The structural half: the import could not resolve even if written.

    `/src/api` declares neither the modeling entry nor any distribution it
    publishes, and its own lockfile — the resolved set the serving image is
    built from — contains no `model`. That is what makes the reachability
    assertion above hold for imports nobody has written yet, rather than only
    for the tree as it stands today.
    """
    declared = declared_third_party(SERVING_ENTRY)
    sources = manifest(SERVING_ENTRY).get("tool", {}).get("uv", {}).get("sources", {})
    locked = locked_distributions(SERVING_ENTRY)
    name = normalize(MODELING_PACKAGE)

    assert name not in declared, f"/src/{SERVING_ENTRY} declares the modeling entry as a dependency"
    assert name not in {normalize(key) for key in sources}, (
        f"/src/{SERVING_ENTRY} resolves the modeling entry from a local path"
    )
    assert name not in locked, (
        f"/src/{SERVING_ENTRY}'s resolved set contains {MODELING_PACKAGE!r}, so an ingestion "
        f"import would resolve inside the request-serving image"
    )


def test_the_ingest_console_entry_belongs_to_the_modeling_entry_alone() -> None:
    """FR-044 from the other direction: the job has one invocation path.

    The `ingest` console script is declared by `/src/model` and by no other
    entry (ADR-0011). A serving entry declaring it would put the job's entry
    point inside the request-serving image even with no import anywhere — the
    script is installed by the package that declares it.
    """
    declaring = {
        entry
        for entry in ("api", "gateway", "model")
        if "ingest" in manifest(entry).get("project", {}).get("scripts", {})
    }
    assert declaring == {"model"}, (
        f"the `ingest` console entry is declared by {sorted(declaring)}; it belongs to the "
        f"modeling entry alone, because the entry that declares a script is the one that "
        f"installs it"
    )
