"""The frozen set aborts before it measures, and publishes its own ceiling.

Spec FR-043, FR-050, Principle VI. The digest is only worth having if the
refusal happens *first*: a harness that loaded, measured, and then checked would
emit a number computed against a set nobody agreed to, and a number once emitted
gets read. These assertions are about **ordering**, not just about detection.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from retrieval.evaluation_set.harness import (
    MANIFEST_NAME,
    QUERIES_NAME,
    EvaluationSetError,
    digest_for,
    load_frozen_set,
    outcomes_at_k,
    reciprocal_ranks,
)

COMMITTED = Path(__file__).resolve().parent / "evaluation_set"
DATASHEET = COMMITTED / "DATASHEET.md"


@pytest.fixture
def perturbable(tmp_path: Path) -> Iterator[Path]:
    """A copy of the committed set, safe to damage."""
    target = tmp_path / "evaluation_set"
    shutil.copytree(COMMITTED, target)
    yield target
    shutil.rmtree(target, ignore_errors=True)


def test_the_committed_set_verifies() -> None:
    """The baseline. Without it every refusal below could be vacuous."""
    frozen = load_frozen_set(COMMITTED)
    assert len(frozen) > 0
    assert frozen.digest == digest_for(COMMITTED / QUERIES_NAME)


def test_a_perturbed_set_aborts_before_returning_any_query(perturbable: Path) -> None:
    """The assertion this file exists for.

    Not "the digest mismatches" — that a hash detects a changed byte is a
    property of SHA-256, not of this code. What is asserted is that **no query
    is returned**, so nothing downstream can compute a figure from a modified
    set even if it ignored the exception.
    """
    queries_path = perturbable / QUERIES_NAME
    document = json.loads(queries_path.read_text(encoding="utf-8"))
    document["queries"][0]["text"] = "a question nobody froze"
    queries_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(EvaluationSetError) as raised:
        load_frozen_set(perturbable)
    assert "digest" in str(raised.value)
    # The exception carries both digests, so the failure names what changed
    # rather than only that something did.
    assert digest_for(queries_path) in str(raised.value)


def test_adding_a_query_is_caught(perturbable: Path) -> None:
    """Growing the set is a modification, not an extension.

    Worth its own case because "we only added queries" is the change most likely
    to be argued as harmless, and it moves every figure measured against the set.
    """
    queries_path = perturbable / QUERIES_NAME
    document = json.loads(queries_path.read_text(encoding="utf-8"))
    document["queries"].append({"query_id": "q-999", "text": "an extra", "relevant_chunk_ids": []})
    queries_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(EvaluationSetError, match="digest"):
        load_frozen_set(perturbable)


def test_a_manifest_with_no_digest_is_refused(perturbable: Path) -> None:
    """An absent digest is a refusal, not an unverified pass."""
    manifest_path = perturbable / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["sha256"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(EvaluationSetError, match="no sha256"):
        load_frozen_set(perturbable)


def test_a_rewritten_manifest_cannot_bless_a_modified_set(perturbable: Path) -> None:
    """Updating the digest to match is a *decision*, and it leaves a trace.

    This is the one attack the digest cannot stop, so what is asserted is what
    it *does* guarantee: the manifest and the queries move together, in a commit
    someone reviews, rather than the set drifting under a stale manifest.
    """
    queries_path = perturbable / QUERIES_NAME
    document = json.loads(queries_path.read_text(encoding="utf-8"))
    document["queries"][0]["text"] = "changed"
    payload = json.dumps(document, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    queries_path.write_bytes(payload)
    manifest_path = perturbable / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"] = hashlib.sha256(payload).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    reloaded = load_frozen_set(perturbable)
    assert reloaded.digest != load_frozen_set(COMMITTED).digest, (
        "a re-blessed set must carry a different digest from the committed one, so "
        "the change is visible in review rather than silent"
    )


def test_every_query_must_be_attempted(perturbable: Path) -> None:
    """A figure covers the whole set or it is not that figure.

    Dropping a query that returned nothing would compute recall over the
    queries that worked — a different and flattering statistic.
    """
    frozen = load_frozen_set(perturbable)
    partial = {frozen.queries[0].query_id: ["whatever"]}
    with pytest.raises(EvaluationSetError, match="must be attempted"):
        outcomes_at_k(frozen, partial, k=5)


def test_a_query_retrieving_nothing_is_a_miss_not_an_absence() -> None:
    """Zero is an outcome. Dropping it would flatter the figure."""
    frozen = load_frozen_set(COMMITTED)
    empty = {query.query_id: [] for query in frozen.queries}
    assert outcomes_at_k(frozen, empty, k=5) == [False] * len(frozen)
    assert reciprocal_ranks(frozen, empty) == [0.0] * len(frozen)


# ---------------------------------------------------------------------------
# FR-050: the datasheet Data Provenance requires of every synthetic dataset
# ---------------------------------------------------------------------------


def test_the_set_ships_a_datasheet() -> None:
    """§Data Provenance requires one of *every* synthetic dataset.

    The judgements are generator-derived, which makes this a synthetic dataset
    as surely as the corpus is. FR-016 covers the vendored models thoroughly,
    and that thoroughness is exactly why the gap in the generated dataset was
    easy to miss.
    """
    assert DATASHEET.is_file(), (
        "the frozen evaluation set ships no DATASHEET.md; Data Provenance requires "
        "a datasheet disclosing the generative assumptions of every synthetic dataset"
    )


@pytest.mark.parametrize(
    "disclosure",
    ["generator", "seed", "draw method", "query count", "ceiling"],
)
def test_the_datasheet_discloses_its_generative_assumptions(disclosure: str) -> None:
    """Each disclosure FR-050 names, present by name."""
    text = DATASHEET.read_text(encoding="utf-8").lower()
    assert disclosure in text, f"the datasheet does not disclose the {disclosure}"


def test_the_datasheet_states_the_figure_is_a_ceiling() -> None:
    """The disclosure that matters most, and the easiest to leave out.

    Every query is answerable by construction, so recall measured here is an
    **upper bound** on real-world performance rather than an estimate of it. A
    datasheet omitting that would let a high number be read as a good one.
    """
    text = DATASHEET.read_text(encoding="utf-8").lower()
    assert "upper bound" in text
    assert "answerable by construction" in text
