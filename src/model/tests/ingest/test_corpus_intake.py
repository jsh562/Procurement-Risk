"""Unit tier for corpus intake: the reader, the id transform, and the type set.

`plan.md` §Testing Strategy puts id minting in the test-after unit tier —
filesystem fixtures, no database. The oracle is **specified**: FR-002 states the
transform in three steps and E003's `ck_document__id_format` states what the
result must satisfy, so each assertion below is against written intent rather
than against the implementation's own output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from model.corpus.manifest import LAYER_REAL, LAYER_SYNTHETIC, content_hash_of_file
from model.ingest.documents import (
    DOCUMENT_TYPES,
    SHARED_LIBRARY_PROJECT,
    DocumentError,
    build_document,
    build_documents,
    classify_type,
    mint_document_id,
)
from model.ingest.manifest_reader import (
    CorpusDocument,
    ManifestReadError,
    iter_entries,
    manifest_digests,
    verify_hash,
)


@pytest.fixture(scope="module")
def corpus() -> tuple[CorpusDocument, ...]:
    return tuple(iter_entries())


# ---------------------------------------------------------------------------
# FR-001 / FR-005 — the reader
# ---------------------------------------------------------------------------


def test_every_manifest_entry_resolves_to_a_committed_file(corpus) -> None:
    """FR-001: enumeration is through the manifests and every entry opens."""
    assert len(corpus) == 51, "26 real specifications plus 25 synthetic transmittals"
    assert all(document.path.is_file() for document in corpus)
    assert {document.layer for document in corpus} == {LAYER_REAL, LAYER_SYNTHETIC}


def test_the_enumeration_order_is_fixed(corpus) -> None:
    """FR-017's determinism rests on the corpus being enumerated the same way."""
    again = tuple(iter_entries())
    assert [d.path for d in again] == [d.path for d in corpus]
    keys = [(d.location_id, d.entry.location) for d in corpus]
    assert keys == sorted(keys)


def test_the_recorded_content_hash_verifies_for_every_document(corpus) -> None:
    """FR-005, over the whole corpus rather than a sample."""
    for document in corpus:
        assert verify_hash(document) == document.content_hash


def test_a_changed_file_fails_before_it_is_parsed(corpus, tmp_path: Path) -> None:
    """FR-005's failing direction: a mismatch raises, it does not return a flag."""
    original = corpus[0]
    altered = tmp_path / original.path.name
    altered.write_bytes(original.path.read_bytes() + b"%% one more byte")
    moved = CorpusDocument(
        location_id=original.location_id,
        layer=original.layer,
        project_id=original.project_id,
        entry=original.entry,
        path=altered,
    )
    with pytest.raises(ManifestReadError, match="FR-005"):
        verify_hash(moved)
    assert content_hash_of_file(altered) != original.content_hash


def test_manifest_digests_are_recorded_per_manifest() -> None:
    """`ingestion_run.corpus_manifest_digests` records what was enumerated."""
    digests = manifest_digests()
    assert len(digests) == 6, "one real location and five synthetic projects"
    assert all(digest.startswith("sha256:") for digest in digests)


# ---------------------------------------------------------------------------
# FR-002 — the identifier transform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("UFGS-23-52-00", "ufgs-23-52-00"),
        ("PRJ-001-T0002-R0", "prj-001-t0002-r0"),
        ("ufgs 23 52 43 00 20", "ufgs-23-52-43-00-20"),
        # A *run* of characters outside the alphabet collapses to one hyphen.
        ("a___b", "a-b"),
        ("--lead-and-trail--", "lead-and-trail"),
        ("Mixed.Case_Stem", "mixed-case-stem"),
    ],
)
def test_the_three_step_transform(stem: str, expected: str) -> None:
    """FR-002, step for step: lower-case, collapse, strip."""
    assert mint_document_id(stem) == expected


@pytest.mark.parametrize("stem", ["", "---", "ab", "@@", "x" * 200])
def test_a_stem_that_does_not_transform_fails_naming_the_file(stem: str) -> None:
    """FR-002: not truncated, not padded, not coerced."""
    with pytest.raises(DocumentError, match="FR-002"):
        mint_document_id(stem, source="data/corpus/real/ufgs/x.pdf")


def test_the_corpus_mints_51_distinct_identifiers(corpus) -> None:
    records = build_documents(corpus)
    assert len({record.document_id for record in records}) == len(records) == 51


def test_two_files_minting_one_identifier_abort_before_any_row(corpus) -> None:
    """FR-052: the check is corpus-wide and names both files."""
    first = corpus[0]
    twin = CorpusDocument(
        location_id=first.location_id,
        layer=first.layer,
        project_id=first.project_id,
        entry=first.entry,
        path=first.path.with_name(first.path.name.upper()),
    )
    with pytest.raises(DocumentError, match="FR-052") as raised:
        build_documents([*corpus, twin])
    assert first.path.name.lower() in str(raised.value).lower()


# ---------------------------------------------------------------------------
# FR-003 / FR-004 / FR-006 — type, project, provenance
# ---------------------------------------------------------------------------


def test_the_type_set_is_closed(corpus) -> None:
    assert classify_type(LAYER_REAL) == "specification"
    assert classify_type(LAYER_SYNTHETIC) == "transmittal"
    with pytest.raises(DocumentError, match="FR-006"):
        classify_type("SCANNED")
    assert {build_document(d).document_type for d in corpus} == DOCUMENT_TYPES


def test_real_specifications_sit_under_the_shared_library_project(corpus) -> None:
    """FR-003 / AD-007: `PRJ-000`, and nothing synthetic may claim it."""
    for document in corpus:
        record = build_document(document)
        if record.source_kind == LAYER_REAL:
            assert record.project_id == SHARED_LIBRARY_PROJECT
        else:
            assert record.project_id != SHARED_LIBRARY_PROJECT
            assert record.project_id == document.project_id


def test_synthetic_provenance_never_includes_retrieval_provenance(corpus) -> None:
    """FR-004, in the direction Principle I cares about."""
    for document in corpus:
        record = build_document(document)
        if record.source_kind == LAYER_SYNTHETIC:
            assert record.source_ref is None
            assert record.issuing_body is None
            assert record.retrieval_date is None
            assert record.generator_id and record.generation_seed
            assert record.roster_hash and record.fixture_hashes
        else:
            assert record.generator_id is None
            assert record.roster_hash is None
            assert record.fixture_hashes is None
            assert record.source_ref.startswith("https://")
            assert record.issuing_body and record.retrieval_date


def test_the_license_basis_is_carried_whole(corpus) -> None:
    """FR-004: `unchanged` is not `the basis id and nothing else`."""
    record = build_document(corpus[0])
    assert "basis_id" in record.license_basis
    assert "statute" in record.license_basis or "statement" in record.license_basis
