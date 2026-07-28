"""FR-017 / SC-007: identical boundaries across four environment dimensions.

A determinism claim with no pinned versions and no varied environment is
unverifiable (research §What makes a test-strategy requirement well-specified),
so this file varies the four dimensions the corpus generator's own reproduction
rule names and requires the *boundary set* to be bit-identical across all of
them:

| Dimension | How it is varied |
|---|---|
| **Process** | every comparison run is a fresh interpreter, not a second call in this one |
| **Hash seed** | `PYTHONHASHSEED` is set to two different values, and to `random` |
| **Working directory** | the child runs from a temporary directory, not the entry root |
| **Enumeration order** | the corpus enumeration is reversed before the document is selected |

The oracle is a **derived** one and is labelled as such: it compares two runs of
the same implementation, so it proves reproducibility and not correctness. What
each chunk *should* be is carried by FR-010's containment check and FR-014's
budget, not by this file.

The compared value is the whole boundary set — ordinal, page, boundary class,
structural identifier, and a digest of the body text — rather than a chunk
count, because two different cuts can produce the same number of chunks.

Two documents, one per layer: a real UFGS section (the ladder, page breaks, and
sentence splitting all exercised) and a synthetic transmittal (field blocks).
The real one is the shortest in the corpus, so the suite stays affordable.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# The shortest real section (16 pages) and one transmittal — enough to reach all
# three boundary classes without chunking the whole corpus four times.
REAL_DOCUMENT = "ufgs-23-52-43-00-20"
SYNTHETIC_DOCUMENT = "prj-001-t0002-r0"

_CHILD = """
import hashlib, json, sys
from model.ingest.manifest_reader import iter_entries
from model.ingest.documents import build_documents
from model.ingest.chunker import chunk_document

document_id, reverse = sys.argv[1], sys.argv[2] == "reverse"
entries = list(iter_entries())
if reverse:
    entries.reverse()
record = next(r for r in build_documents(entries) if r.document_id == document_id)
chunking = chunk_document(record)
rows = [
    [
        chunk.ordinal,
        chunk.page_number,
        chunk.boundary_class,
        chunk.structural_identifier,
        hashlib.sha256(chunk.body_text.encode("utf-8")).hexdigest(),
    ]
    for chunk in chunking.chunks
]
print(json.dumps({"version": chunking.chunker_version, "rows": rows}, sort_keys=True))
"""


def _boundaries(document_id: str, *, hash_seed: str, cwd: Path, reverse: bool) -> str:
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = hash_seed
    environment["PYTHONWARNINGS"] = "ignore"
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", _CHILD, document_id, "reverse" if reverse else "forward"],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return completed.stdout.strip().splitlines()[-1]


@pytest.mark.parametrize("document_id", [REAL_DOCUMENT, SYNTHETIC_DOCUMENT])
def test_boundaries_are_identical_across_four_dimensions(document_id: str, tmp_path: Path) -> None:
    """SC-007: same input tuple, same boundaries and same ordinals."""
    entry_root = Path(__file__).resolve().parents[2]

    baseline = _boundaries(document_id, hash_seed="0", cwd=entry_root, reverse=False)
    variants = {
        "hash seed 1, temporary cwd": _boundaries(
            document_id, hash_seed="1", cwd=tmp_path, reverse=False
        ),
        "random hash seed, reversed enumeration": _boundaries(
            document_id, hash_seed="random", cwd=tmp_path, reverse=True
        ),
    }
    for label, observed in variants.items():
        assert observed == baseline, f"boundaries differ under {label}"


def test_the_comparison_would_notice_a_moved_boundary() -> None:
    """The failing direction: the compared value is sensitive to a single cut.

    A determinism check comparing something that cannot differ passes forever.
    Perturbing one row of the recorded boundary set must change the compared
    string, which is what makes the assertions above load-bearing.
    """
    entry_root = Path(__file__).resolve().parents[2]
    baseline = _boundaries(SYNTHETIC_DOCUMENT, hash_seed="0", cwd=entry_root, reverse=False)
    perturbed = baseline.replace('"structural"', '"sentence"', 1)
    assert perturbed != baseline
