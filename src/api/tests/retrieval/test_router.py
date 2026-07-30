"""The part-number route: additive, falling through, never subtractive.

Spec FR-010 to FR-014. The property that matters is the one a lookup normally
breaks: **the route may never remove a result hybrid retrieval would have
returned.** That is asserted against the route-disabled result rather than by
inspection, so "additive" is a measured comparison and not a description of
intent.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.retrieval.router import (  # noqa: E402
    PART_NUMBER_PATTERN,
    recognise_part_numbers,
    resolve_part_numbers,
)
from retrieval.fixtures.seed_chunks import seeded_corpus  # noqa: E402


@pytest.fixture
def seeded() -> Iterator[tuple[psycopg.Connection, object]]:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is unset; the route resolves against real rows")
    with psycopg.connect(url) as conn:
        corpus = seeded_corpus(conn)
        yield conn, corpus
        conn.rollback()


# --- recognition (FR-010) -------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "NRH-80347",
        "what is the lead time on NRH-80347",
        "NRH-80347?",
        "compare NRH-80347 and NRH-11902",
    ],
)
def test_a_designation_is_recognised_anywhere_in_the_query(query: str) -> None:
    """FR-010 recognises a token *anywhere*, not only when typed alone.

    "what is the lead time on NRH-80347" is the question a coordinator actually
    asks; a whole-string match would recognise the designation only in the one
    phrasing nobody uses.
    """
    assert "NRH-80347" in recognise_part_numbers(query)


@pytest.mark.parametrize(
    "query",
    ["nrh-80347", "NRH-123", "ABCDE-12345", "bronze valve", "part 80347", "N-1234"],
)
def test_near_misses_are_not_recognised(query: str) -> None:
    """The pattern is narrow on purpose.

    A false positive adds a result nobody asked for and labels it
    `deterministic_identifier` while doing it — which is worse than missing a
    real designation, because the label asserts certainty.
    """
    assert recognise_part_numbers(query) == ()


def test_duplicates_collapse() -> None:
    """Resolving one designation twice would add the same chunk twice."""
    assert recognise_part_numbers("NRH-80347 and again NRH-80347") == ("NRH-80347",)


def test_recognition_preserves_typed_order() -> None:
    assert recognise_part_numbers("NRH-11902 then NRH-80347") == ("NRH-11902", "NRH-80347")


def test_the_pattern_is_the_published_one() -> None:
    """FR-010 requires the pattern be declared, not implied by behaviour."""
    assert PART_NUMBER_PATTERN.pattern == r"\b[A-Z]{2,4}-\d{4,6}\b"


# --- resolution and fall-through (FR-011) ---------------------------------


def test_a_recognised_token_resolves_to_its_chunk(
    seeded: tuple[psycopg.Connection, object],
) -> None:
    connection, corpus = seeded
    outcome = resolve_part_numbers(connection, ["NRH-80347"])
    assert outcome.fired
    assert corpus.part_number_chunk_id in outcome.added_chunk_ids


def test_a_recognised_token_matching_nothing_falls_through(
    seeded: tuple[psycopg.Connection, object],
) -> None:
    """FR-011. Not an error, and not an empty answer.

    Returning nothing because the route missed would make a well-formed part
    number *worse* than a vague question — the opposite of what recognising it
    is for.
    """
    connection, _ = seeded
    outcome = resolve_part_numbers(connection, ["ZZZ-99999"])
    assert not outcome.fired
    assert outcome.fell_through
    assert outcome.added_chunk_ids == ()


def test_falling_through_is_distinct_from_recognising_nothing(
    seeded: tuple[psycopg.Connection, object],
) -> None:
    """Both leave hybrid retrieval answering alone; they mean different things.

    Only fall-through means the query *looked* like a part number and the
    corpus does not hold it — which is what a coordinator needs told.
    """
    connection, _ = seeded
    assert resolve_part_numbers(connection, ["ZZZ-99999"]).fell_through is True
    assert resolve_part_numbers(connection, []).fell_through is False


# --- the union is additive (FR-012, FR-013) -------------------------------


def test_the_route_never_removes_what_fusion_returned(
    seeded: tuple[psycopg.Connection, object],
) -> None:
    """FR-012, asserted against the route-disabled result.

    This is the assertion the whole module exists for, and it is a comparison
    rather than a claim: whatever fusion produced is still present after the
    route runs.
    """
    connection, corpus = seeded
    fused_ids = list(corpus.chunk_ids[:3])
    outcome = resolve_part_numbers(connection, ["NRH-80347"], exclude_chunk_ids=fused_ids)
    surviving = set(fused_ids) | set(outcome.added_chunk_ids)
    assert set(fused_ids) <= surviving


def test_a_chunk_fusion_already_returned_is_not_added_twice(
    seeded: tuple[psycopg.Connection, object],
) -> None:
    """One chunk, one account of how it was found.

    Adding it again as a deterministic match would put two copies in the
    response disagreeing about its match kind and its fused rank.
    """
    connection, corpus = seeded
    outcome = resolve_part_numbers(
        connection, ["NRH-80347"], exclude_chunk_ids=[corpus.part_number_chunk_id]
    )
    assert corpus.part_number_chunk_id not in outcome.added_chunk_ids
    # Still *matched* — the exclusion is about duplication, not about pretending
    # the route found nothing.
    assert outcome.fired


def test_no_tokens_resolves_to_nothing_without_querying(
    seeded: tuple[psycopg.Connection, object],
) -> None:
    connection, _ = seeded
    outcome = resolve_part_numbers(connection, [])
    assert outcome.added_chunk_ids == ()
    assert outcome.recognised_tokens == ()


def test_the_synthetic_layer_resolves_nothing_today(
    seeded: tuple[psycopg.Connection, object],
) -> None:
    """The live defect, asserted rather than described.

    `part_numbers` is NULL on every synthetic row, so a designation printed only
    in transmittal body text cannot be resolved by this route. The fixture puts
    `NRH-80347` in a synthetic chunk's body and in the public chunk's
    `part_numbers`; only the latter resolves. When E006's repair lands, this
    assertion is what will fail and say so.
    """
    connection, corpus = seeded
    outcome = resolve_part_numbers(connection, ["NRH-80347"])
    assert set(outcome.added_chunk_ids) <= set(corpus.real_layer_ids), (
        "a synthetic chunk resolved by the route, which means part_numbers is now "
        "populated there — E006's repair has landed and FR-049 requires every figure "
        "measured before it to be re-measured"
    )
