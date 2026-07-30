"""Derive the frozen set's judgements from the corpus they judge.

FR-043 requires judgements drawn from the generator's pre-render document model.
The committed set was hand-typed against that model, and the identifiers went
stale: `queries.json` named chunks like `556b9305-1f4b-1ba0-2c47-ec4a13a3a37d`
while the fixture produces `556b9305-242f-7766-9faf-84a98ceec320` — same leading
group, different derivation. Every judgement missed, so the gate measured recall
0.000 and MRR 0.000 for a reason that had nothing to do with retrieval.

**A digest cannot catch that.** It certifies the set has not changed since it was
frozen, which was true: the set was frozen wrong. Verification against the
*corpus* is a different question from verification against the *bytes*, and only
the second was being asked.

So the judgements are computed here from the fixture rows rather than typed, and
`test_evaluation_set.py` asserts the committed set equals what this produces.
A judgement that stops matching its corpus now fails a test instead of silently
zeroing a headline figure.

**Still generator-derived, and the ceiling is unchanged.** The predicates below
read the fixture's own record of what each row contains — its `body_text` and
`part_numbers`, which are the pre-render model for this corpus — rather than
running retrieval and calling the winners relevant. Judging by retrieval output
would make the measurement circular: recall would be 1.0 by construction and
would measure nothing at all.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

__all__ = ["GENERATOR_ID", "QUERY_SPECS", "SEED", "build_query_set", "serialise"]

GENERATOR_ID = "e008-evaluation-set"

#: Fixed and recorded, so the bootstrap over this set draws the same resamples
#: on every run. Read from the manifest by anything that measures.
SEED = 0

#: One query per retrieval behaviour the P1 criteria measure, each paired with
#: the predicate that decides relevance from the document model.
#:
#: The predicates are deliberately about *content*, not about ranking. `tie-a`
#: and `tie-b` are judged relevant to the valve query because they describe a
#: bronze flanged pressure relief valve — the fact that they exist to exercise
#: the tie-break is a property of the fixture, not a reason to withhold a
#: judgement the document model supports.
QUERY_SPECS: tuple[tuple[str, str, Callable[[Any], bool]], ...] = (
    (
        "q-001",
        "bronze pressure relief valve",
        # Lexical: the words are in the text.
        lambda row: (
            "pressure relief valve" in row.body_text.lower() and "bronze" in row.body_text.lower()
        ),
    ),
    (
        "q-002",
        "circulator pump mechanical seals",
        # Dense: phrased as a coordinator would ask, matched on the subject.
        lambda row: "circulator pump" in row.body_text.lower(),
    ),
    (
        "q-003",
        "NRH-80347",
        # Part-number lookup. Both layers count: the identifier appears in the
        # real layer's `part_numbers` column and in the synthetic layer's body
        # text, and FR-012 makes the deterministic route additive over both.
        lambda row: "NRH-80347" in (row.part_numbers or "") or "NRH-80347" in row.body_text,
    ),
)


def build_query_set(
    rows: Sequence[Any],
    identifier: Callable[[str], str],
) -> dict[str, Any]:
    """The query set as a document, judged against `rows`.

    `identifier` maps a fixture row key to the chunk id the fixture will seed,
    passed in rather than imported so this module does not reach into the
    fixture's private helpers — and so the mapping used to build the set is
    provably the one the fixture uses to seed it.
    """
    queries = []
    for query_id, text, is_relevant in QUERY_SPECS:
        judged = sorted(identifier(row.key) for row in rows if is_relevant(row))
        if not judged:
            msg = (
                f"{query_id} judges no chunk in the corpus. A query with no relevant "
                f"chunk is unanswerable, and FR-043's ceiling claim rests on every query "
                f"being answerable by construction."
            )
            raise ValueError(msg)
        queries.append({"query_id": query_id, "relevant_chunk_ids": judged, "text": text})
    return {"queries": queries}


def serialise(document: dict[str, Any]) -> bytes:
    """The exact bytes the digest is taken over.

    Sorted keys and a fixed indent, because the digest is over bytes and two
    serialisations of one document would fail verification for a reason nobody
    could find. Trailing newline included — most editors add one, and a set that
    fails its digest after a whitespace-only save is a set people learn to
    re-hash without reading.
    """
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
