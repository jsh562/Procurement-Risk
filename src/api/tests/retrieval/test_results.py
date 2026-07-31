"""Empty is empty, short is short, and the match kind is carried.

Spec FR-009 and FR-013. The temptation this guards against is not a bug someone
writes by accident — it is the reasonable-sounding improvement: if fusion
returns two results and the caller asked for ten, top it up from the weaker arm.
That turns "there are two passages about this" into "here are ten passages",
which is the invisible mistake Principle III bars.
"""

from __future__ import annotations

import pytest

from api.retrieval.results import MatchKind, results_from_rows

#: A well-formed projected row, in `PROJECTED_COLUMNS` order.
ROW = ("chunk-1", "spec-0001", "specification", "PRJ-001", 12, "Bronze bodied valve.")
ROW_2 = ("chunk-2", "spec-0001", "specification", "PRJ-001", 14, "Circulator pump.")


def test_no_rows_produces_no_results() -> None:
    """An empty set is returned as empty, not as a nearest-neighbour fallback."""
    assert results_from_rows([]) == []


def test_a_short_set_is_not_padded() -> None:
    """Two rows in, two results out. No top-up to a target count."""
    results = results_from_rows([ROW, ROW_2])
    assert len(results) == 2


def test_order_is_preserved() -> None:
    """Row *i* becomes result *i*.

    The ranking is decided by the statement; a projection that reordered would
    silently discard it, and the fused ranks below would then disagree with the
    order they are attached to.
    """
    results = results_from_rows([ROW, ROW_2])
    assert [result.chunk_id for result in results] == ["chunk-1", "chunk-2"]


def test_ranked_results_carry_their_one_based_position() -> None:
    results = results_from_rows([ROW, ROW_2])
    assert [result.fused_rank for result in results] == [1, 2]


def test_an_unranked_result_carries_no_fused_rank() -> None:
    """A deterministic route match has no fused rank, and must not be given one.

    FR-012 unions route matches additively. A route match carrying a fabricated
    rank would sort among the ranked results as though it had been scored,
    which is precisely the additive union becoming a substitution.
    """
    results = results_from_rows([ROW], match_kind=MatchKind.DETERMINISTIC_IDENTIFIER, ranked=False)
    assert results[0].fused_rank is None
    assert results[0].match_kind is MatchKind.DETERMINISTIC_IDENTIFIER


def test_the_match_kind_is_a_closed_vocabulary() -> None:
    """Two values, and a machine can tell them apart.

    FR-013 requires a deterministic identifier hit be distinguishable from a
    ranked relevance hit *by a machine*. A free-form string would be
    distinguishable only by a reader who already knew the convention.
    """
    assert {kind.value for kind in MatchKind} == {
        "deterministic_identifier",
        "ranked_relevance",
    }


def test_every_provenance_field_comes_from_the_row() -> None:
    """The projection reads the record, field for field."""
    result = results_from_rows([ROW])[0]
    assert (
        result.chunk_id,
        result.document_id,
        result.document_type,
        result.project_id,
        result.page_number,
        result.body_text,
    ) == ROW


def test_results_are_immutable() -> None:
    """A page that could be reassigned after construction would defeat the factory."""
    result = results_from_rows([ROW])[0]
    with pytest.raises(Exception, match="frozen|immutable|cannot assign"):
        result.page_number = 99  # type: ignore[misc]
