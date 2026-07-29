"""FR-023 / FR-048 (AD-001): only `model.llm` may import the gateway.

E006 places its model-facing code under `model.llm` rather than adding a new
package and widening the computation-boundary contract to name it. A forbidden
contract covers its named package's descendants, so `model.llm` -> `model.compute`
is already forbidden by the contract E001 committed and *placement is the whole
mechanism* — `src/model/pyproject.toml` needs no edit, which T006 evidenced by
planting a `model.llm` module importing `model.compute` and observing
`lint-imports` exit 1.

Placement being the mechanism is exactly what leaves a hole, and this file is
that hole's cover. A model-facing module placed *outside* `model.llm` escapes
the contract entirely: the contract names `model.llm` as its source, so a
module at `model.ingest.extraction` reaching the provider through the gateway
violates nothing it declares. `test_single_import_site.py` does not see it
either — that scan looks for the provider *distribution*, and the module in
question imports `gateway`, never `anthropic`.

So the assertion here is a placement assertion rather than an import-graph one:
every module under `/src/model` that imports `gateway` at all must live inside
`model/llm/`. Where the check lives follows the same rule the rest of this
directory does — it asserts a boundary between the modeling entry's packages
and a *shared* package neither of them owns, which is the narrow `/tests`
exception `test_layout.py` and `test_migration_ranges.py` already sit under.

Imports are read from the parsed syntax tree rather than matched textually.
`test_single_import_site.py` is deliberately textual because it is chasing a
name assembled at runtime, which no import statement records; here the rule is
about import statements specifically, and a textual scan would report this
docstring.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_PACKAGE = REPO_ROOT / "src" / "model" / "src" / "model"

#: The one package permitted to import it, relative to `MODEL_PACKAGE`.
PERMITTED_SUBPACKAGE = "llm"

GATEWAY_PACKAGE = "gateway"

EXCLUDED_DIRS = frozenset({"__pycache__", ".venv", ".ruff_cache", ".pytest_cache"})


@dataclass(frozen=True)
class GatewayImport:
    """One module importing the gateway, with the statement that does it."""

    path: Path
    line_number: int
    statement: str


def _python_files(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if path.is_file() and not any(part in EXCLUDED_DIRS for part in path.parts)
    ]


def _imports_gateway(node: ast.AST) -> bool:
    """True for every syntactic form that binds the gateway package.

    All four are the same violation and none is more honest than the others:
    `import gateway`, `import gateway.api as g`, `from gateway import call`,
    `from gateway.models import Request`. A check that recognised only the
    first would be satisfied by rewriting the statement.
    """
    if isinstance(node, ast.Import):
        return any(
            alias.name == GATEWAY_PACKAGE or alias.name.startswith(f"{GATEWAY_PACKAGE}.")
            for alias in node.names
        )
    if isinstance(node, ast.ImportFrom):
        # `node.level > 0` is a relative import, which cannot reach outside the
        # `model` package and so can never name the gateway.
        if node.level or node.module is None:
            return False
        return node.module == GATEWAY_PACKAGE or node.module.startswith(f"{GATEWAY_PACKAGE}.")
    return False


def gateway_importers(root: Path) -> list[GatewayImport]:
    """Every Python file under ``root`` whose source imports the gateway."""
    found: list[GatewayImport] = []
    for path in _python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):  # pragma: no cover - not on a green tree
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for node in ast.walk(tree):
            if not _imports_gateway(node):
                continue
            # Narrowing only: `_imports_gateway` is True for no other node type,
            # and `lineno` is what the failure message needs to be actionable.
            assert isinstance(node, ast.Import | ast.ImportFrom)
            line = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
            found.append(GatewayImport(path=path, line_number=node.lineno, statement=line))
    return found


def _misplaced(importers: list[GatewayImport]) -> list[GatewayImport]:
    permitted = MODEL_PACKAGE / PERMITTED_SUBPACKAGE
    return [entry for entry in importers if permitted not in entry.path.parents]


def test_only_model_llm_imports_the_gateway() -> None:
    """FR-048. The assertion the whole file exists for."""
    misplaced = _misplaced(gateway_importers(MODEL_PACKAGE))
    rendered = "\n".join(
        f"  {entry.path.relative_to(REPO_ROOT).as_posix()}:{entry.line_number}: {entry.statement}"
        for entry in misplaced
    )
    assert not misplaced, (
        f"{len(misplaced)} module(s) outside `model.{PERMITTED_SUBPACKAGE}` import the "
        f"gateway:\n{rendered}\n"
        f"The computation-boundary contract names `model.{PERMITTED_SUBPACKAGE}` as its "
        f"source module, so a model-facing module placed anywhere else is outside the "
        f"contract rather than in violation of it — it would pass `lint-imports` while "
        f"reaching the provider. Move it under "
        f"{(MODEL_PACKAGE / PERMITTED_SUBPACKAGE).relative_to(REPO_ROOT).as_posix()}/."
    )


def test_the_scan_reads_the_whole_modeling_package() -> None:
    """A placement check is only as good as the set of files it looked at.

    `model.llm` is empty until E006's extraction module lands, so
    `test_only_model_llm_imports_the_gateway` currently passes over zero
    gateway imports — and it would pass identically if this scan read nothing
    at all. This asserts the denominator is real: the modeling package's own
    subpackages are inside it, so a module added to any of them is seen.
    """
    scanned = set(_python_files(MODEL_PACKAGE))
    assert scanned, f"the placement scan reads no file under {MODEL_PACKAGE}"

    subpackages = sorted(
        path.name
        for path in MODEL_PACKAGE.iterdir()
        if path.is_dir() and path.name not in EXCLUDED_DIRS
    )
    assert PERMITTED_SUBPACKAGE in subpackages, (
        f"`model.{PERMITTED_SUBPACKAGE}` does not exist; the permitted location this "
        f"check names is not a real package"
    )
    for subpackage in subpackages:
        covered = {path for path in scanned if MODEL_PACKAGE / subpackage in path.parents}
        assert covered, (
            f"`model.{subpackage}` exists but the placement scan reads no file in it; "
            f"a gateway import placed there would be invisible to FR-048"
        )


@pytest.mark.parametrize(
    "statement",
    [
        "import gateway",
        "import gateway.api",
        "import gateway.api as client",
        "from gateway import call",
        "from gateway.models import Request",
    ],
)
def test_the_check_reports_a_seeded_gateway_import_outside_model_llm(
    tmp_path: Path, statement: str
) -> None:
    """Positive control: a check that cannot fail proves nothing.

    Seeded in five syntactic forms rather than one. All five bind the same
    package, so a check recognising only `import gateway` would be satisfied by
    rewriting the statement — which is not a fix, it is the same module in the
    same wrong place.
    """
    module = tmp_path / "ingest" / "extraction.py"
    module.parent.mkdir(parents=True)
    module.write_text(f"{statement}\n", encoding="utf-8")

    found = gateway_importers(tmp_path)
    assert [entry.path for entry in found] == [module], (
        f"the scan missed a seeded gateway import written as {statement!r}"
    )
    assert found[0].line_number == 1


def test_the_check_permits_a_gateway_import_inside_model_llm() -> None:
    """The other direction, and not a formality.

    A check that reported every gateway import would also pass on a correct
    tree today — `model.llm` is empty — and would then fail the moment E006's
    extraction module lands, which is the one file FR-023 requires to import
    the gateway. This pins that `model/llm/` is permitted rather than merely
    unpopulated.
    """
    permitted = MODEL_PACKAGE / PERMITTED_SUBPACKAGE / "extraction.py"
    entry = GatewayImport(path=permitted, line_number=1, statement="from gateway import call")
    assert _misplaced([entry]) == [], (
        f"a gateway import inside `model.{PERMITTED_SUBPACKAGE}` was reported as "
        f"misplaced; FR-023 requires exactly that module to import it"
    )


def test_a_relative_import_is_not_mistaken_for_the_gateway() -> None:
    """`from .gateway import x` inside `model.ingest` names a sibling module,
    not the shared package, and reporting it would make the check cry wolf on
    code that violates nothing."""
    module = ast.parse("from .gateway import call\n").body[0]
    assert not _imports_gateway(module)


def test_a_module_merely_named_like_the_gateway_is_not_reported() -> None:
    """`gateway_config` starts with the package name and is a different package.
    Prefix matching without the dot would report it."""
    for source in ("import gateway_config\n", "from gateway_config import settings\n"):
        module = ast.parse(source).body[0]
        assert not _imports_gateway(module), f"{source.strip()!r} was reported as a gateway import"
