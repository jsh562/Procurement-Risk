"""Load the frozen evaluation set, or abort before anything is measured.

Spec FR-043, Principle VI. The set is frozen, hashed and committed *before* any
tuning run, and this module is what makes that a mechanism rather than an
intention: it verifies the digest and refuses on mismatch **before returning
any queries**, so a perturbed set cannot produce a figure at all.

The ordering matters and is the whole design. A harness that loaded, measured,
and then checked would emit a number computed against a set nobody agreed to —
and a number, once emitted, gets read. Aborting first means the failure mode is
a missing figure, which is visible, rather than a wrong one, which is not.

**What the digest can and cannot see.** It detects modification of the set. It
cannot detect *repeated measurement against it*, which is the mechanism that
turns a frozen set into a training set — and no artifact in this repository can
count runs across machines and branches. So the discipline is not a run budget,
which would be a rule enforced by memory: any ranking-parameter change made
after a figure is measured is recorded as a decision, the set re-measured, and
**both figures emitted together**. Publishing only the later one satisfies
re-measurement and hides the tuning.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    "MANIFEST_NAME",
    "QUERIES_NAME",
    "EvaluationSet",
    "EvaluationSetError",
    "Query",
    "load_frozen_set",
]

QUERIES_NAME: Final = "queries.json"
MANIFEST_NAME: Final = "manifest.json"

#: Where the frozen set lives. Under `data/`, not beside this module, because
#: `project-instructions.md` §Source Code Layout places "data, corpus manifests,
#: and datasheets under `data/`" and a committed, hashed corpus with a
#: release-gate role is data rather than a test fixture. `data/reranker/` set the
#: same precedent in this epic. The *harness* stays here, in the tier that runs
#: it, because it is code.
COMMITTED_SET = Path(__file__).resolve().parents[5] / "data" / "evaluation_set"


class EvaluationSetError(RuntimeError):
    """The evaluation set is absent, malformed, or does not match its digest.

    One type for every failure, because the consequence is identical in each
    case: no measurement may be taken, and no figure may be emitted.
    """


@dataclass(frozen=True)
class Query:
    """One evaluation query and the chunks judged relevant to it."""

    query_id: str
    text: str
    relevant_chunk_ids: frozenset[str]


@dataclass(frozen=True)
class EvaluationSet:
    """The frozen set, its digest, and the ceiling its judgements imply."""

    queries: tuple[Query, ...]
    digest: str
    generator_id: str
    seed: int
    #: Every query is answerable by construction, because the judgements come
    #: from the generator's pre-render document model. So a recall figure
    #: measured here is an **upper bound on real-world performance**, not an
    #: estimate of it, and FR-043 requires it published as such.
    answerable_by_construction: bool = True

    def __len__(self) -> int:
        return len(self.queries)


def _digest_of(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load_frozen_set(directory: Path) -> EvaluationSet:
    """Verify the committed set's digest and return it, or abort.

    Raises:
        EvaluationSetError: The set or its manifest is missing, malformed, or
            the recorded digest does not match the committed queries. Every
            case aborts **before** any query is returned, so no measurement can
            be taken against an unverified set.
    """
    queries_path = directory / QUERIES_NAME
    manifest_path = directory / MANIFEST_NAME
    try:
        raw = queries_path.read_bytes()
    except OSError as exc:
        raise EvaluationSetError(
            f"cannot read the evaluation set at {queries_path}: {exc}"
        ) from exc
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvaluationSetError(f"cannot read the manifest at {manifest_path}: {exc}") from exc
    except ValueError as exc:
        raise EvaluationSetError(f"{manifest_path} is not valid JSON: {exc}") from exc

    recorded = manifest.get("sha256")
    if not recorded:
        raise EvaluationSetError(f"{manifest_path} records no sha256 for the query set")
    observed = _digest_of(raw)
    if observed != recorded:
        msg = (
            f"the evaluation set does not match its recorded digest.\n"
            f"  recorded: {recorded}\n"
            f"  observed: {observed}\n"
            f"Refusing before any measurement is taken (FR-043, Principle VI): a figure "
            f"computed against a modified set would be a number nobody agreed to, and a "
            f"number once emitted gets read."
        )
        raise EvaluationSetError(msg)

    try:
        document = json.loads(raw.decode("utf-8"))
        queries = tuple(
            Query(
                query_id=str(entry["query_id"]),
                text=str(entry["text"]),
                relevant_chunk_ids=frozenset(str(value) for value in entry["relevant_chunk_ids"]),
            )
            for entry in document["queries"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EvaluationSetError(f"{queries_path} is malformed: {exc}") from exc

    if not queries:
        raise EvaluationSetError(
            f"{queries_path} holds no queries. An empty set would produce a figure over "
            f"nothing, which FR-042 refuses rather than reports."
        )

    return EvaluationSet(
        queries=queries,
        digest=observed,
        generator_id=str(manifest.get("generator_id", "")),
        seed=int(manifest.get("seed", 0)),
    )


def digest_for(queries_path: Path) -> str:
    """The digest a manifest should record for `queries_path`.

    Exposed so the committed manifest is produced by the same function that
    verifies it — two implementations of one hash is how a set comes to fail
    verification for a reason nobody can find.
    """
    return _digest_of(queries_path.read_bytes())


def relevant_for(evaluation_set: EvaluationSet, query_id: str) -> frozenset[str]:
    """The judged-relevant chunks for one query."""
    for query in evaluation_set.queries:
        if query.query_id == query_id:
            return query.relevant_chunk_ids
    msg = f"{query_id!r} is not in the frozen set"
    raise EvaluationSetError(msg)


def outcomes_at_k(
    evaluation_set: EvaluationSet,
    retrieved: dict[str, Sequence[str]],
    *,
    k: int,
) -> list[bool]:
    """One hit-or-miss outcome per query, in the set's own order.

    The population is the **whole** frozen set, not the queries that returned
    something. A query that retrieved nothing is a miss, not an absence — and
    dropping it would compute recall over the queries that worked, which is a
    different and flattering statistic.
    """
    missing = [
        query.query_id for query in evaluation_set.queries if query.query_id not in retrieved
    ]
    if missing:
        msg = (
            f"no retrieval was recorded for {len(missing)} of {len(evaluation_set)} queries "
            f"({missing[:3]}...). Every query in the frozen set must be attempted, or the "
            f"figure covers a population smaller than the one it names."
        )
        raise EvaluationSetError(msg)
    return [
        bool(set(retrieved[query.query_id][:k]) & query.relevant_chunk_ids)
        for query in evaluation_set.queries
    ]


def reciprocal_ranks(
    evaluation_set: EvaluationSet,
    retrieved: dict[str, Sequence[str]],
) -> list[float]:
    """One reciprocal rank per query, zero where nothing relevant was retrieved.

    Zero is a real outcome rather than a missing value, for the same reason the
    outcomes above count a miss: the mean is over the set, not over the
    successes.
    """
    values: list[float] = []
    for query in evaluation_set.queries:
        ordered = retrieved.get(query.query_id, ())
        rank = next(
            (
                position
                for position, chunk_id in enumerate(ordered, start=1)
                if chunk_id in query.relevant_chunk_ids
            ),
            None,
        )
        values.append(0.0 if rank is None else 1.0 / rank)
    return values
