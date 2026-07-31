"""The projection factory is the only way a result gets built.

Spec FR-008, AD-004, Principle I. The guarantee this file protects is that a
page number cannot originate anywhere but the chunk record it belongs to.

**Asserted by scanning the source, not by testing behaviour.** A behavioural
test can only show that the paths it exercises project correctly; it says
nothing about a path added next month that constructs a `RetrievalResult`
directly with a page from somewhere else. The scan sees every construction site
in the package, including the one nobody remembers writing — which is the one
that matters, because a wrong page is a plausible small integer and looks
exactly like a right one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from api.retrieval.results import MatchKind, RetrievalResult, results_from_rows

RETRIEVAL_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "api" / "retrieval"

#: The one function permitted to build a result.
FACTORY = "_from_chunk_row"

#: Where that factory lives. Construction inside its own module is the factory
#: itself doing its job.
FACTORY_MODULE = "results.py"


def _construction_sites(root: Path) -> list[tuple[Path, int]]:
    """Every `RetrievalResult(...)` call in the package, with its line."""
    found: list[tuple[Path, int]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                name = getattr(target, "id", None) or getattr(target, "attr", None)
                if name == "RetrievalResult":
                    found.append((path, node.lineno))
    return found


def test_the_factory_is_the_only_construction_site() -> None:
    """Nothing outside `results.py` builds a result directly.

    The failure message names the file and line, because "somewhere in the
    package" is not actionable and this check is worth acting on quickly.
    """
    outside = [
        (path, line)
        for path, line in _construction_sites(RETRIEVAL_PACKAGE)
        if path.name != FACTORY_MODULE
    ]
    rendered = "\n".join(f"  {path.name}:{line}" for path, line in outside)
    assert not outside, (
        f"{len(outside)} construction site(s) outside {FACTORY_MODULE}:\n{rendered}\n"
        f"A result built anywhere but {FACTORY}() can carry a page that did not "
        f"come from the chunk row, which is the one field a reader follows and "
        f"cannot check."
    )


def test_the_scan_reads_something() -> None:
    """A source scan is only as good as the files it looked at.

    Without this, `test_the_factory_is_the_only_construction_site` would pass
    identically if the glob matched nothing — which is how a path change turns a
    guarantee into a no-op with no failure anywhere.
    """
    files = list(RETRIEVAL_PACKAGE.rglob("*.py"))
    assert files, f"the provenance scan reads no file under {RETRIEVAL_PACKAGE}"
    assert any(path.name == FACTORY_MODULE for path in files), (
        f"{FACTORY_MODULE} is not among the scanned files, so the factory itself was not checked"
    )
    assert _construction_sites(RETRIEVAL_PACKAGE), (
        "the scan found no construction site at all, including inside the factory — "
        "it is matching nothing and would pass whatever the package contained"
    )


def test_a_result_cannot_be_built_with_a_zero_page() -> None:
    """Pages are one-based, enforced on read as well as on write.

    A zero here would mean the projection lost a value between the row and the
    result, which is a defect the schema's own constraint cannot catch because
    it only sees writes.
    """
    with pytest.raises(ValueError, match="one-based"):
        RetrievalResult(
            chunk_id="c",
            document_id="d",
            document_type="specification",
            project_id="PRJ-001",
            page_number=0,
            body_text="text",
            match_kind=MatchKind.RANKED_RELEVANCE,
            fused_rank=1,
        )


def test_a_short_projection_raises_rather_than_guessing() -> None:
    """A row with the wrong column count is refused, not partially read.

    Positional projection is what keeps provenance honest, and it is also what
    makes a column-order change dangerous. Refusing is the difference between a
    loud failure and a result whose page came from whatever column happened to
    be fifth.
    """
    with pytest.raises(ValueError, match="projected columns"):
        results_from_rows([("c", "d", "specification")])
