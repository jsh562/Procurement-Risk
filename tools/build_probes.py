"""Build the committed two-layer parity probe set (T027, FR-019).

Selection is mechanical and recorded on every probe: document, page, and line
range. Run from `src/model` through the entry's own environment.
"""

from __future__ import annotations

import json
from pathlib import Path

from model.ingest.documents import build_documents
from model.ingest.manifest_reader import iter_entries
from model.ingest.parse import read_pages

REPO = Path(__file__).resolve()
OUT = Path("S:/claudecode/KayaDemoProcurementRisk2/data/encoder/probes.json")

records = build_documents(iter_entries())
real = [r for r in records if r.source_kind == "REAL"]
synthetic = [r for r in records if r.source_kind == "SYNTHETIC"]

probes: list[dict[str, object]] = []


def add(record, page_number: int, start: int, stop: int, note: str) -> None:
    pages = read_pages(record.path)
    page = next(p for p in pages if p.number == page_number)
    lines = page.lines[start:stop]
    text = "\n".join(lines)
    if not text.strip():
        raise SystemExit(f"empty probe from {record.document_id} p{page_number}")
    probes.append(
        {
            "probe_id": f"{record.document_id}-p{page_number:04d}-l{start:03d}-{stop:03d}",
            "layer": record.source_kind,
            "document_id": record.document_id,
            "page_number": page_number,
            "line_range": [start, stop],
            "note": note,
            "text": text,
        }
    )


# Real layer: three documents spread across the sorted 26, three page regions
# each -- a reference list, structured body prose with bracketed markup, and a
# heading ladder.
for index in (0, 12, 25):
    record = real[index]
    pages = read_pages(record.path)
    total = len(pages)
    for fraction, note in ((0.12, "reference list"), (0.4, "body prose"), (0.8, "ladder")):
        number = max(1, min(total, int(total * fraction) + 1))
        add(record, number, 0, 8, note)

# One deliberately over-cap real probe: a whole page, which exceeds 254 content
# pieces and therefore exercises the truncation both encoders must apply
# identically.
add(real[12], max(1, len(read_pages(real[12].path)) // 2), 0, 60, "over cap")

# Synthetic layer: five projects, two documents apiece where available, the
# label/value block that is what extraction actually reads.
seen_projects: dict[str, int] = {}
for record in synthetic:
    taken = seen_projects.get(record.project_id, 0)
    if taken >= 2:
        continue
    seen_projects[record.project_id] = taken + 1
    add(record, 1, 0, 12, "transmittal field block")

# One over-cap synthetic probe, for the same reason as the real one.
add(synthetic[0], 1, 0, 80, "over cap")

payload = {
    "purpose": (
        "FR-019 / ADR-0018 parity probe set. Spans both corpus layers. Selection is "
        "mechanical and recorded per probe: document identifier, page number, and the "
        "half-open line range taken from that page through the committed reader."
    ),
    "declared_bounds": {
        "cosine_similarity_min": 0.999999,
        "max_absolute_per_dimension_difference": 1e-05,
        "declared_before": "the first comparison was run; see specs/.../plan.md FR-019",
    },
    "probes": probes,
}
# newline="\n" is load-bearing: the digest in digests.json is taken over these
# bytes, and Python's text mode translates "\n" to "\r\n" on Windows. Writing
# without it recorded a CRLF digest for a file git committed as LF, which passed
# on the author's machine and failed on the Linux runner.
OUT.write_text(
    json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    encoding="utf-8",
    newline="\n",
)
print(f"{len(probes)} probes", {p["layer"] for p in probes})
