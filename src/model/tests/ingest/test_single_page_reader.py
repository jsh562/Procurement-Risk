"""SC-037 / FR-008: the ingestion package declares no second page reader.

This is a **contract check over an absence**, not a measured agreement between
two readers. FR-008 says so explicitly, and the distinction matters for what a
pass here is worth: it proves the ingestion package obtains page text by calling
`model.corpus.derive`, and it proves nothing at all about whether that reader
reads any particular page correctly. That claim is carried by FR-010's total
containment check and FR-011's inspection bound, and is disclosed rather than
implied.

Three prohibited things, each with its own failing direction (`plan.md`
§Testing Strategy, Architecture tier):

1. a call to `extract_words` — the pdfplumber entry point whose tolerances
   `derive.WORD_EXTRACTION` pins;
2. a tolerance mapping — any binding of `x_tolerance`, `y_tolerance`,
   `keep_blank_chars`, or `use_text_flow`, which is what a second tolerance map
   looks like whatever it is named;
3. a second normalization — a call to `unicodedata.normalize`, which is the
   operation `derive._normalized` performs and the only one that can produce a
   comparison form disagreeing with it.

Decided by reading the package's **source** with `ast`, not by importing it and
inspecting objects: a module that builds its tolerance mapping at run time from
a dict comprehension would be invisible to an attribute scan and is exactly the
shape the prohibition has to catch. The scan is also positive, not only
negative: it requires the package to actually reach `model.corpus.derive`, so
deleting `parse.py` altogether cannot turn this file green.

Entry-local, under `src/model/tests/`, because the claim is about one package
inside the model entry — it does not need the root `/tests` cross-entry
exception (`plan.md` FR-008 row).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# `src/model/tests/ingest/` → the package under test.
INGEST_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "model" / "ingest"

#: The keyword names `derive.WORD_EXTRACTION` binds. Any of them bound anywhere
#: in the ingestion package is a second tolerance mapping by definition.
TOLERANCE_KEYS = frozenset({"x_tolerance", "y_tolerance", "keep_blank_chars", "use_text_flow"})

PROBE = "# planted probe\n"


def _sources() -> list[Path]:
    modules = sorted(INGEST_PACKAGE.rglob("*.py"))
    assert modules, f"no module found under {INGEST_PACKAGE}"
    return modules


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _called_names(tree: ast.Module) -> set[str]:
    """Every simple and attribute call target in a module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _bound_keys(tree: ast.Module) -> set[str]:
    """Every string key and keyword name a module binds.

    Covers all three ways a tolerance map can be written: a keyword argument
    (`extract_words(x_tolerance=1.0)`), a dict literal key
    (`{"x_tolerance": 1.0}`), and a plain assignment (`x_tolerance = 1.0`).
    """
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg is not None:
            keys.add(node.arg)
        elif isinstance(node, ast.Dict):
            keys.update(
                item.value
                for item in node.keys
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            keys.add(node.id)
    return keys


@pytest.mark.parametrize("path", _sources(), ids=lambda path: path.name)
def test_no_module_calls_extract_words(path: Path) -> None:
    """SC-037, part 1: pdfplumber's word extraction is `derive`'s call to make."""
    assert "extract_words" not in _called_names(_tree(path))


@pytest.mark.parametrize("path", _sources(), ids=lambda path: path.name)
def test_no_module_declares_a_tolerance_mapping(path: Path) -> None:
    """SC-037, part 2: the tolerances live in one place, `derive.WORD_EXTRACTION`."""
    declared = _bound_keys(_tree(path)) & TOLERANCE_KEYS
    assert not declared, f"{path.name} binds {sorted(declared)}"


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("path", _sources(), ids=lambda path: path.name)
def test_no_module_defines_a_second_normalization(path: Path) -> None:
    """SC-037, part 3: NFC plus whitespace collapse is `derive._normalized`'s.

    Two directions, because a second normalization can be written either way.
    `unicodedata` is not imported anywhere in the package — that is the module
    the operation lives in — and no call target is the bare name `normalize`.
    `normalize_page_text` and `normalized_page_text` are different names and are
    the sanctioned route, so neither is caught by this.
    """
    tree = _tree(path)
    assert "unicodedata" not in _imported_modules(tree), f"{path.name} imports unicodedata"
    assert "normalize" not in _called_names(tree), f"{path.name} calls normalize()"


def test_the_package_reaches_the_committed_reader() -> None:
    """The positive half: an absence check passes trivially over an absence.

    Deleting `parse.py` would satisfy all three prohibitions above, so the
    package is also required to import the reader it is supposed to be calling.
    """
    importers = {
        path.name
        for path in _sources()
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ImportFrom) and node.module == "model.corpus.derive"
    }
    assert "parse.py" in importers, f"no ingestion module imports the committed reader: {importers}"


def test_the_scan_rejects_a_planted_tolerance_map(tmp_path: Path) -> None:
    """The failing direction, demonstrated rather than asserted.

    A check whose failing direction has never been observed is a check nobody
    has evidence works. Each of the three prohibitions is planted into a
    throwaway module and the scan must object to it.
    """
    planted = tmp_path / "planted.py"

    planted.write_text(PROBE + "WORDS = {'x_tolerance': 1.0, 'y_tolerance': 2.0}\n", "utf-8")
    assert _bound_keys(_tree(planted)) & TOLERANCE_KEYS == {"x_tolerance", "y_tolerance"}

    planted.write_text(PROBE + "def f(page):\n    return page.extract_words()\n", "utf-8")
    assert "extract_words" in _called_names(_tree(planted))

    planted.write_text(
        PROBE + "import unicodedata\nT = unicodedata.normalize('NFC', 'a')\n", "utf-8"
    )
    assert "normalize" in _called_names(_tree(planted))
    assert "unicodedata" in _imported_modules(_tree(planted))
