"""A committed corpus for the retrieval integration tier, seeded per test.

**The merge gate runs against an empty `chunk` table.** `.github/workflows/verify.yml`
applies the migration chain and never ingests: the `reproduce` job aborts before
writing chunks by design. So every integration assertion about the fusion
statement would pass over zero rows — a green suite proving nothing, which is
the shape this epic has met repeatedly. This fixture is what gives those
assertions something to be true or false about.

Seeded **inside a transaction the caller rolls back**, following the unit and
integration convention rather than E006's end-to-end `seed.py`, which commits to
a database of its own. Nothing here writes to a shared database, and the rows
vanish when the test's transaction unwinds.

The corpus is deliberately small and deliberately shaped. Each property below
exists because some assertion needs it, and a fixture that was merely "some
chunks" would let those assertions pass vacuously:

- **Both provenance layers.** `REAL` and `SYNTHETIC` documents, because FR-005
  reports the empty-weighted-field proportion *per layer* and a single-layer
  corpus makes that partition untestable.
- **`part_numbers` NULL on the synthetic layer.** This is the live defect, not
  an oversight: E006 writes NULL there on every row, so `search_vector`'s
  weight-B arm is empty corpus-wide. FR-005 exists to publish that, and a
  fixture that populated the column would make the disclosure untestable and
  the figure it publishes a fiction.
- **An engineered tie at the fetch-depth boundary.** Two chunks share a lexical
  rank and are separated only by `chunk_id`, so T030 can assert the candidate
  set is stable at the 50-row cut. Without a tie there, a missing per-arm
  tie-break passes.
- **A part number in body text only.** `NRH-80347` appears in `body_text` and
  nowhere structured, which is what the deterministic route must find and what
  the lexical arm tokenizes badly.

Embeddings are synthetic unit vectors, not encoder output. The dense arm's
*ordering* is what the integration tier asserts, and deriving vectors from a
seeded generator keeps this file offline and its rows reproducible from a clean
checkout. Anything asserting encoder fidelity belongs to the parity gate, which
already does it against independently produced references.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

import psycopg

__all__ = [
    "EMBEDDING_DIMENSION",
    "SeededCorpus",
    "seeded_corpus",
]

#: Matches the `vector(384)` column the schema declares.
EMBEDDING_DIMENSION: Final = 384

_PROJECT: Final = "PRJ-001"

#: The tie pair. Same body text, so the same lexical rank, separated only by
#: identifier — which is exactly what the per-arm tie-break has to resolve.
_TIE_BODY: Final = "Ashvale Industrial pressure relief valve, bronze body, flanged."


@dataclass(frozen=True)
class SeededCorpus:
    """What was seeded, so a test can assert against it without re-querying."""

    chunk_ids: tuple[str, ...]
    tie_pair: tuple[str, str]
    part_number_chunk_id: str
    real_layer_ids: tuple[str, ...]
    synthetic_layer_ids: tuple[str, ...]

    @property
    def size(self) -> int:
        return len(self.chunk_ids)


def _deterministic_id(seed: str) -> str:
    """A stable UUID derived from `seed`.

    Derived rather than random so a failing assertion names the same identifier
    on every run — a fixture whose identifiers move makes a golden ordering
    unrepeatable, which is the property FR-020 is about.
    """
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def _unit_vector(seed: str) -> list[float]:
    """A reproducible unit vector for `seed`.

    Built from the seed's digest so it is stable across runs and machines, then
    normalized because the column stores normalized vectors and pgvector's
    cosine distance is the inner product only when they are.
    """
    raw = hashlib.sha256(seed.encode("utf-8")).digest()
    values = [((raw[i % len(raw)] + i) % 251) / 251.0 - 0.5 for i in range(EMBEDDING_DIMENSION)]
    norm = sum(value * value for value in values) ** 0.5
    return [value / norm for value in values]


@dataclass(frozen=True)
class _Row:
    key: str
    document_id: str
    document_type: str
    source_kind: str
    page_number: int
    ordinal: int
    body_text: str
    heading: str | None
    spec_section: str | None
    part_numbers: str | None


def _rows() -> tuple[_Row, ...]:
    return (
        # REAL layer: fully weighted — heading, spec section and part numbers all
        # present. This is the arm the field weighting was designed for, and the
        # comparison that makes the synthetic layer's emptiness visible.
        _Row(
            "real-valve",
            "spec-0001",
            "specification",
            "REAL",
            12,
            0,
            "Pressure relief valves shall be bronze bodied and flanged per section 22 05 23.",
            "Pressure Relief Valves",
            "22 05 23",
            "NRH-80347",
        ),
        _Row(
            "real-pump",
            "spec-0001",
            "specification",
            "REAL",
            14,
            1,
            "Circulator pumps shall be inline, bronze fitted, with mechanical seals.",
            "Circulator Pumps",
            "23 21 23",
            "NRH-11902",
        ),
        # SYNTHETIC layer: part_numbers NULL on every row, which is the corpus
        # state E006 actually writes. heading is NULL on transmittal field
        # blocks and spec_section is NULL on transmittals because their code
        # appears as body text — so all three weighted arms are empty here and
        # only the D-weighted body contributes.
        _Row(
            "synth-transmittal",
            "trn-0001",
            "transmittal",
            "SYNTHETIC",
            1,
            0,
            "Transmittal 26 11 13 covering valve NRH-80347 from Ashvale Ind.",
            None,
            None,
            None,
        ),
        _Row(
            "synth-field-block",
            "trn-0001",
            "transmittal",
            "SYNTHETIC",
            1,
            1,
            "Submitted by ASHVALE INDUSTRIAL on behalf of the mechanical subcontractor.",
            None,
            None,
            None,
        ),
        # The engineered tie: identical body text, so identical lexical rank.
        # Only `chunk_id` separates them, which is what the per-arm tie-break
        # inside each CTE has to resolve. Without it the candidate set at the
        # cut varies between runs and the reranker scores different rows.
        _Row("tie-a", "trn-0001", "transmittal", "SYNTHETIC", 2, 2, _TIE_BODY, None, None, None),
        _Row("tie-b", "trn-0001", "transmittal", "SYNTHETIC", 2, 3, _TIE_BODY, None, None, None),
    )


#: A well-formed digest for the synthetic layer's provenance columns. The schema
#: requires `^sha256:[0-9a-f]{64}$` on `roster_hash` and the same prefix on every
#: member of `fixture_hashes`, so a placeholder string will not do.
_SYNTHETIC_DIGEST: Final = "sha256:" + hashlib.sha256(b"e008-retrieval-fixture").hexdigest()


def _documents() -> tuple[dict[str, object], ...]:
    """The two documents the chunks hang from, one per provenance layer.

    Every column here is dictated by the schema's own provenance regime, which
    is strict and asymmetric: a REAL document must carry an issuing body, a
    retrieval date and a source reference, and must carry *no* generator
    fields; a SYNTHETIC one must carry a generator, a seed, a generated-on date,
    at least one `sha256:`-prefixed fixture hash and a roster hash, and must
    carry none of the REAL ones. The constraints are what force this fixture to
    state real provenance rather than plausible-looking filler.
    """
    return (
        {
            "document_id": "spec-0001",
            "document_type": "specification",
            "project_id": _PROJECT,
            "title": "Mechanical Specification, Division 22",
            "source_kind": "REAL",
            "license_basis": "public-domain",
            "source_ref": "https://example.invalid/spec-0001",
            "retrieval_date": date(2026, 1, 15),
            "issuing_body": "Ashvale Industrial Works",
            "generator_id": None,
            "generation_seed": None,
            "generated_at": None,
            "fixture_hashes": None,
            "roster_hash": None,
        },
        {
            "document_id": "trn-0001",
            "document_type": "transmittal",
            "project_id": _PROJECT,
            "title": "Submittal Transmittal 0001",
            "source_kind": "SYNTHETIC",
            "license_basis": "generated-for-this-repository",
            "source_ref": None,
            "retrieval_date": None,
            "issuing_body": None,
            "generator_id": "e008-retrieval-fixture",
            "generation_seed": "0",
            "generated_at": date(2026, 7, 29),
            "fixture_hashes": [_SYNTHETIC_DIGEST],
            "roster_hash": _SYNTHETIC_DIGEST,
        },
    )


def seeded_corpus(connection: psycopg.Connection) -> SeededCorpus:
    """Seed the fixture corpus on `connection` and describe what was written.

    The connection is **taken, not created**, so the caller owns the transaction
    and rolls it back. A function that opened its own would leave rows behind on
    a failing assertion, and the next run would then be measuring a corpus that
    no test intended.

    Returns:
        A `SeededCorpus` naming the identifiers a test needs to assert against —
        the tie pair, the part-number chunk, and the per-layer partition — so an
        assertion states its expectation rather than rediscovering it with a
        query that could drift from what was seeded.
    """
    rows = _rows()
    with connection.cursor() as cursor:
        for doc in _documents():
            cursor.execute(
                """
                INSERT INTO document (
                    document_id, document_type, project_id, title, source_kind,
                    license_basis, source_ref, retrieval_date, issuing_body,
                    generator_id, generation_seed, generated_at, fixture_hashes,
                    roster_hash, loaded_at
                )
                VALUES (
                    %(document_id)s, %(document_type)s, %(project_id)s, %(title)s,
                    %(source_kind)s, %(license_basis)s, %(source_ref)s,
                    %(retrieval_date)s, %(issuing_body)s, %(generator_id)s,
                    %(generation_seed)s, %(generated_at)s, %(fixture_hashes)s,
                    %(roster_hash)s, now()
                )
                ON CONFLICT DO NOTHING
                """,
                doc,
            )
        for row in rows:
            cursor.execute(
                """
                INSERT INTO chunk (
                    chunk_id, document_id, document_type, project_id,
                    page_number, ordinal, spec_section, heading, part_numbers,
                    body_text, embedding, embedding_model_id, embedding_model_revision
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
                """,
                (
                    _deterministic_id(row.key),
                    row.document_id,
                    row.document_type,
                    _PROJECT,
                    row.page_number,
                    row.ordinal,
                    row.spec_section,
                    row.heading,
                    row.part_numbers,
                    row.body_text,
                    "[" + ",".join(repr(v) for v in _unit_vector(row.key)) + "]",
                    "sentence-transformers/all-MiniLM-L6-v2",
                    "main",
                ),
            )
    return SeededCorpus(
        chunk_ids=tuple(_deterministic_id(row.key) for row in rows),
        tie_pair=(_deterministic_id("tie-a"), _deterministic_id("tie-b")),
        part_number_chunk_id=_deterministic_id("real-valve"),
        real_layer_ids=tuple(_deterministic_id(r.key) for r in rows if r.source_kind == "REAL"),
        synthetic_layer_ids=tuple(
            _deterministic_id(r.key) for r in rows if r.source_kind == "SYNTHETIC"
        ),
    )


def layer_of(corpus: SeededCorpus, chunk_id: str) -> str:
    """Which provenance layer `chunk_id` belongs to.

    Provided so FR-005's per-layer assertion reads from the fixture's own record
    rather than re-deriving the partition with a join — two derivations of one
    partition is how a test comes to assert about a grouping the code does not
    actually use.
    """
    if chunk_id in corpus.real_layer_ids:
        return "REAL"
    if chunk_id in corpus.synthetic_layer_ids:
        return "SYNTHETIC"
    msg = f"{chunk_id} was not seeded by this fixture"
    raise KeyError(msg)


def all_body_texts() -> Sequence[str]:
    """Every body text this fixture seeds, in seeding order."""
    return tuple(row.body_text for row in _rows())
