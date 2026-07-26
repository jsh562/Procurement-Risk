"""FR-006b / FR-007: the manifest writer, MS-1…MS-6, and the layer asymmetry.

PB-5 of `plan.md` §Property-Based Test Specification — *two manifests with
equal content serialize to byte-identical files whatever order their entries
and keys were supplied in, and two differing in any recorded value serialize to
different bytes* — plus the boundary cases that specification names separately
for this file: a duplicate manifest key, a pair of `location` values colliding
under case folding, and the empty `irregularity_classes` list.

The NFC/NFD pair, the single-page document, the empty per-page text and the
degradation-parameter cases belong to the document model and are covered by
`test_corpus_model_hash.py`; they are not repeated here.

Both directions of PB-5 matter and fail on different defects. The invariant
direction is what makes VR-042's byte comparison achievable at all; the
metamorphic direction is what shows the serialization does not *collapse*
distinct content, which the committed corpus alone cannot exercise because
there content is held fixed and only the writer varies.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from model.corpus import manifest as manifest_module
from model.corpus.manifest import (
    COMMON_FIELDS,
    GENERATION_INPUT_PATHS,
    IRREGULARITY_CLASSES,
    REAL_ONLY_FIELDS,
    SYNTHETIC_ONLY_FIELDS,
    Manifest,
    ManifestError,
    RealEntry,
    RealLicenseBasis,
    SyntheticEntry,
    SyntheticLicenseBasis,
    canonical_manifest_bytes,
    content_hash_of_file,
    generation_input_digests,
    roster_digest,
    sha256_of_bytes,
    sha256_of_file,
    upstream_digest_of_response,
    write_manifest,
)
from model.roster.reader import read_roster

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

A_DIGEST = "sha256:" + "a" * 64
ANOTHER_DIGEST = "sha256:" + "b" * 64

# --------------------------------------------------------------------------
# Strategies. Locations are drawn from a lowercase alphabet and required
# unique, so the generated population never trips the duplicate-key or
# case-collision rules those boundary cases below assert deliberately.
# --------------------------------------------------------------------------

digests = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64).map(
    lambda hexed: "sha256:" + hexed
)
location_stems = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=1, max_size=8)
locations = location_stems.filter(lambda stem: stem[0].isalnum()).map(lambda stem: stem + ".pdf")
short_text = st.text(
    alphabet=st.characters(categories=("Lu", "Ll", "Nd"), codec="utf-8"), min_size=1, max_size=12
)
sections = st.builds(
    lambda a, b, c: f"{a:02d} {b:02d} {c:02d}",
    st.integers(0, 99),
    st.integers(0, 99),
    st.integers(0, 99),
)
revision_dates = st.builds(
    lambda y, m: f"{y:04d}-{m:02d}", st.integers(1900, 2099), st.integers(1, 12)
)
generation_dates = st.builds(
    lambda y, m, d: f"{y:04d}-{m:02d}-{d:02d}",
    st.integers(1900, 2099),
    st.integers(1, 12),
    st.integers(1, 28),
)
retrieved_ats = st.builds(
    lambda d, h, m, s: f"{d}T{h:02d}:{m:02d}:{s:02d}Z",
    generation_dates,
    st.integers(0, 23),
    st.integers(0, 59),
    st.integers(0, 59),
)
real_bases = st.builds(RealLicenseBasis, document_identifier=short_text)
synthetic_bases = st.builds(SyntheticLicenseBasis, statement=short_text)
generation_inputs = st.fixed_dictionaries(dict.fromkeys(GENERATION_INPUT_PATHS, digests))


def real_entries(location: str) -> st.SearchStrategy[RealEntry]:
    return st.builds(
        RealEntry,
        location=st.just(location),
        license_basis=real_bases,
        content_hash=digests,
        source_location=short_text.map(lambda stem: f"https://example.invalid/{stem}.pdf"),
        retrieval_response_status=st.just(200),
        retrieved_at=retrieved_ats,
        issuing_body=short_text,
        masterformat_section=sections,
        agency_variant=short_text,
        revision_date=revision_dates,
        upstream_digest=digests,
    )


def synthetic_entries(location: str) -> st.SearchStrategy[SyntheticEntry]:
    return st.builds(
        SyntheticEntry,
        location=st.just(location),
        license_basis=synthetic_bases,
        content_hash=digests,
        generator_id=short_text,
        seed=st.integers(0, 2**31),
        generation_date=generation_dates,
        roster_hash=digests,
        generation_inputs=generation_inputs,
        document_model_hash=digests,
        irregularity_classes=st.lists(st.sampled_from(IRREGULARITY_CLASSES), max_size=5).map(tuple),
    )


@st.composite
def manifests(draw: st.DrawFn, layer: str | None = None) -> Manifest:
    chosen = layer or draw(st.sampled_from(["REAL", "SYNTHETIC"]))
    names = draw(st.lists(locations, min_size=1, max_size=5, unique=True))
    build = real_entries if chosen == "REAL" else synthetic_entries
    entries = tuple(draw(build(name)) for name in names)
    if chosen == "REAL":
        return Manifest(location_id="real/ufgs", entries=entries)
    project_id = draw(st.sampled_from(["PRJ-001", "PRJ-002", "PRJ-003", "PRJ-004", "PRJ-005"]))
    return Manifest(location_id=f"synthetic/{project_id}", entries=entries, project_id=project_id)


# --------------------------------------------------------------------------
# PB-5, first direction — invariant: supply order does not move the bytes
# --------------------------------------------------------------------------


@given(manifest=manifests())
def test_pb5_entry_supply_order_does_not_move_the_bytes(manifest: Manifest) -> None:
    """MS-1. The writer sorts; the caller's order is not a recorded value."""
    reversed_order = Manifest(
        location_id=manifest.location_id,
        entries=tuple(reversed(manifest.entries)),
        project_id=manifest.project_id,
    )
    assert canonical_manifest_bytes(reversed_order) == canonical_manifest_bytes(manifest)


@given(manifest=manifests(layer="SYNTHETIC"))
def test_pb5_mapping_key_supply_order_does_not_move_the_bytes(manifest: Manifest) -> None:
    """MS-3's `sort_keys`, over the one caller-supplied mapping an entry holds."""
    entries = tuple(
        replace(
            entry,
            generation_inputs={
                key: entry.generation_inputs[key] for key in reversed(GENERATION_INPUT_PATHS)
            },
        )
        for entry in manifest.entries
    )
    shuffled = Manifest(
        location_id=manifest.location_id, entries=entries, project_id=manifest.project_id
    )
    assert canonical_manifest_bytes(shuffled) == canonical_manifest_bytes(manifest)


@given(manifest=manifests(layer="SYNTHETIC"))
def test_pb5_irregularity_class_supply_order_and_repetition_do_not_move_the_bytes(
    manifest: Manifest,
) -> None:
    """MS-2: deduplicated and sorted, so a class recorded twice is one class."""
    entries = tuple(
        replace(
            entry,
            irregularity_classes=tuple(reversed(entry.irregularity_classes))
            + entry.irregularity_classes,
        )
        for entry in manifest.entries
    )
    noisy = Manifest(
        location_id=manifest.location_id, entries=entries, project_id=manifest.project_id
    )
    assert canonical_manifest_bytes(noisy) == canonical_manifest_bytes(manifest)


# --------------------------------------------------------------------------
# PB-5, second direction — metamorphic: any changed recorded value moves them
# --------------------------------------------------------------------------

REAL_ALTERNATIVES: dict[str, object] = {
    "location": "renamed-by-the-test.pdf",
    "content_hash": ANOTHER_DIGEST,
    "license_basis": RealLicenseBasis(document_identifier="UFGS 26 05 00 (2020-01)"),
    "source_location": "https://example.invalid/some-other-document.pdf",
    "retrieval_response_status": 206,
    "retrieved_at": "2021-03-04T05:06:07Z",
    "issuing_body": "A Different Issuing Body",
    "masterformat_section": "23 64 26",
    "agency_variant": "A-DIFFERENT-VARIANT",
    "revision_date": "2018-11",
    "upstream_digest": ANOTHER_DIGEST,
}

SYNTHETIC_ALTERNATIVES: dict[str, object] = {
    "location": "renamed-by-the-test.pdf",
    "content_hash": ANOTHER_DIGEST,
    "license_basis": SyntheticLicenseBasis(statement="A different restatement."),
    "generator_id": "a-different-generator",
    "seed": 987_654_321,
    "generation_date": "2019-08-07",
    "roster_hash": ANOTHER_DIGEST,
    "generation_inputs": dict.fromkeys(GENERATION_INPUT_PATHS, ANOTHER_DIGEST),
    "document_model_hash": ANOTHER_DIGEST,
    "irregularity_classes": IRREGULARITY_CLASSES,
}


def _changed(manifest: Manifest, field: str, value: object) -> Manifest:
    head, *rest = manifest.entries
    return Manifest(
        location_id=manifest.location_id,
        entries=(replace(head, **{field: value}), *rest),
        project_id=manifest.project_id,
    )


@given(
    manifest=manifests(layer="REAL"),
    field=st.sampled_from(sorted(REAL_ALTERNATIVES)),
)
def test_pb5_any_changed_real_field_changes_the_bytes(manifest: Manifest, field: str) -> None:
    alternative = REAL_ALTERNATIVES[field]
    assume(getattr(manifest.entries[0], field) != alternative)
    assert canonical_manifest_bytes(_changed(manifest, field, alternative)) != (
        canonical_manifest_bytes(manifest)
    )


@given(
    manifest=manifests(layer="SYNTHETIC"),
    field=st.sampled_from(sorted(SYNTHETIC_ALTERNATIVES)),
)
def test_pb5_any_changed_synthetic_field_changes_the_bytes(manifest: Manifest, field: str) -> None:
    alternative = SYNTHETIC_ALTERNATIVES[field]
    current = getattr(manifest.entries[0], field)
    assume(dict(current) != alternative if field == "generation_inputs" else current != alternative)
    assert canonical_manifest_bytes(_changed(manifest, field, alternative)) != (
        canonical_manifest_bytes(manifest)
    )


def _content(manifest: Manifest) -> tuple:
    """Canonical content: what MS-1…MS-3 hold constant against supply order."""
    return (manifest.location_id, manifest.layer, manifest.project_id, manifest.entries)


@given(first=manifests(), second=manifests())
def test_pb5_distinct_content_never_collapses_to_one_serialization(
    first: Manifest, second: Manifest
) -> None:
    """Injectivity over generated pairs, beside the field-by-field table above:
    the table proves every *named* field participates, this proves no
    combination of them collapses."""
    assume(_content(first) != _content(second))
    assert canonical_manifest_bytes(first) != canonical_manifest_bytes(second)


@given(manifest=manifests())
def test_pb5_equal_content_serializes_identically_across_rebuilds(manifest: Manifest) -> None:
    rebuilt = Manifest(
        location_id=manifest.location_id,
        entries=tuple(replace(entry) for entry in manifest.entries),
        project_id=manifest.project_id,
    )
    assert canonical_manifest_bytes(rebuilt) == canonical_manifest_bytes(manifest)


# --------------------------------------------------------------------------
# Named boundary cases
# --------------------------------------------------------------------------


def a_synthetic_entry(location: str, **overrides: object) -> SyntheticEntry:
    fields: dict[str, object] = {
        "location": location,
        "license_basis": SyntheticLicenseBasis(statement="Generated by this project."),
        "content_hash": A_DIGEST,
        "generator_id": "corpus-generate",
        "seed": 20260725,
        "generation_date": "2026-07-25",
        "roster_hash": A_DIGEST,
        "generation_inputs": dict.fromkeys(GENERATION_INPUT_PATHS, A_DIGEST),
        "document_model_hash": A_DIGEST,
        "irregularity_classes": (),
    }
    fields.update(overrides)
    return SyntheticEntry(**fields)  # type: ignore[arg-type]


def a_real_entry(location: str, **overrides: object) -> RealEntry:
    fields: dict[str, object] = {
        "location": location,
        "license_basis": RealLicenseBasis(document_identifier="UFGS 26 05 00 (2024-05)"),
        "content_hash": A_DIGEST,
        "source_location": "https://example.invalid/ufgs-26-05-00.pdf",
        "retrieval_response_status": 200,
        "retrieved_at": "2026-07-25T12:00:00Z",
        "issuing_body": "U.S. Army Corps of Engineers",
        "masterformat_section": "26 05 00",
        "agency_variant": "UNIFIED",
        "revision_date": "2024-05",
        "upstream_digest": A_DIGEST,
    }
    fields.update(overrides)
    return RealEntry(**fields)  # type: ignore[arg-type]


def test_boundary_a_duplicate_location_is_rejected_rather_than_merged() -> None:
    """VR-011. Two entries under one key is not a last-wins merge; it is a
    manifest that cannot be written."""
    with pytest.raises(ManifestError) as raised:
        Manifest(
            location_id="synthetic/PRJ-001",
            entries=(a_synthetic_entry("sub-0001.pdf"), a_synthetic_entry("sub-0001.pdf")),
            project_id="PRJ-001",
        )
    assert "sub-0001.pdf" in str(raised.value)


def test_boundary_the_written_json_carries_no_duplicate_object_key() -> None:
    """VR-001's read direction, asserted over what this writer emits: parsed
    under a hook that *rejects* duplicates rather than merging them last-wins,
    the emitted bytes must still parse."""

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict:
        keys = [key for key, _ in pairs]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise AssertionError(f"the writer emitted duplicate keys: {duplicates}")
        return dict(pairs)

    manifest = Manifest(
        location_id="synthetic/PRJ-001",
        entries=(a_synthetic_entry("sub-0001.pdf"), a_synthetic_entry("sub-0002.pdf")),
        project_id="PRJ-001",
    )
    parsed = json.loads(
        canonical_manifest_bytes(manifest).decode("utf-8"), object_pairs_hook=reject_duplicates
    )
    assert [entry["location"] for entry in parsed["entries"]] == ["sub-0001.pdf", "sub-0002.pdf"]


def test_boundary_two_locations_colliding_under_case_folding_are_rejected() -> None:
    """VR-068. The two differ in codepoint order, so MS-1 sorts them happily;
    on a case-folding filesystem they name one file, which would make VR-011's
    bijection pass on one platform and fail on another."""
    with pytest.raises(ManifestError) as raised:
        Manifest(
            location_id="synthetic/PRJ-002",
            entries=(a_synthetic_entry("Report.pdf"), a_synthetic_entry("report.pdf")),
            project_id="PRJ-002",
        )
    message = str(raised.value)
    assert "Report.pdf" in message and "report.pdf" in message


def test_boundary_an_empty_irregularity_class_list_is_admissible() -> None:
    """A clean document is a real outcome. The empty list is recorded as an
    empty array — never omitted, which FR-010 would read as a missing field."""
    manifest = Manifest(
        location_id="synthetic/PRJ-003",
        entries=(a_synthetic_entry("sub-0001.pdf", irregularity_classes=()),),
        project_id="PRJ-003",
    )
    payload = json.loads(canonical_manifest_bytes(manifest))
    assert payload["entries"][0]["irregularity_classes"] == []


def test_a_class_outside_the_closed_five_is_rejected() -> None:
    with pytest.raises(ManifestError):
        a_synthetic_entry("sub-0001.pdf", irregularity_classes=("SMUDGED",))


# --------------------------------------------------------------------------
# The layer asymmetry, in both directions (VR-017, VR-027)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("field", sorted(REAL_ONLY_FIELDS))
def test_a_synthetic_entry_cannot_be_constructed_with_a_retrieval_field(field: str) -> None:
    """Not a validation rule that a caller can forget to run: there is no
    attribute slot, so the constructor refuses. A generated document must not
    carry retrieval provenance it does not have."""
    with pytest.raises(TypeError):
        a_synthetic_entry("sub-0001.pdf", **{field: "anything"})


@pytest.mark.parametrize("field", sorted(SYNTHETIC_ONLY_FIELDS))
def test_a_real_entry_cannot_be_constructed_with_a_generation_field(field: str) -> None:
    with pytest.raises(TypeError):
        a_real_entry("ufgs-26-05-00.pdf", **{field: "anything"})


def test_the_written_field_sets_are_exactly_the_applicable_ones() -> None:
    real = a_real_entry("ufgs-26-05-00.pdf").payload()
    synthetic = a_synthetic_entry("sub-0001.pdf").payload()
    assert set(real) == COMMON_FIELDS | REAL_ONLY_FIELDS
    assert set(synthetic) == COMMON_FIELDS | SYNTHETIC_ONLY_FIELDS
    assert set(real) & SYNTHETIC_ONLY_FIELDS == set()
    assert set(synthetic) & REAL_ONLY_FIELDS == set()


def test_a_manifest_holds_exactly_one_layer() -> None:
    """VR-007, made unconstructible rather than checked after the fact."""
    with pytest.raises(ManifestError) as raised:
        Manifest(
            location_id="real/ufgs",
            entries=(a_real_entry("a.pdf"), a_synthetic_entry("b.pdf")),
        )
    assert "one layer" in str(raised.value)


def test_a_real_manifest_carries_no_project_id_and_a_synthetic_one_must() -> None:
    """VR-006, both directions."""
    with pytest.raises(ManifestError):
        Manifest(location_id="real/ufgs", entries=(a_real_entry("a.pdf"),), project_id="PRJ-001")
    with pytest.raises(ManifestError):
        Manifest(location_id="synthetic/PRJ-001", entries=(a_synthetic_entry("a.pdf"),))
    with pytest.raises(ManifestError) as raised:
        Manifest(
            location_id="synthetic/PRJ-001",
            entries=(a_synthetic_entry("a.pdf"),),
            project_id="PRJ-002",
        )
    assert "final segment" in str(raised.value)


def test_an_empty_manifest_is_rejected() -> None:
    """VR-066 at the writer: every 'for each entry' rule passes over nothing."""
    with pytest.raises(ManifestError):
        Manifest(location_id="real/ufgs", entries=())


# --------------------------------------------------------------------------
# Serialization shape (MS-3, MS-4, MS-5, VR-058)
# --------------------------------------------------------------------------


def a_manifest() -> Manifest:
    return Manifest(
        location_id="synthetic/PRJ-004",
        entries=(
            a_synthetic_entry("sub-0002.pdf", irregularity_classes=("SCAN_DEGRADATION",)),
            a_synthetic_entry("sub-0001.pdf", license_basis=SyntheticLicenseBasis("Sección")),
        ),
        project_id="PRJ-004",
    )


def test_ms3_the_serialization_is_indented_sorted_utf8_with_one_trailing_newline() -> None:
    raw = canonical_manifest_bytes(a_manifest())
    text = raw.decode("utf-8")
    assert text.endswith("}\n") and not text.endswith("\n\n")
    assert '\n  "entries": [' in text
    # ensure_ascii=False: non-ASCII survives as itself rather than as \uXXXX.
    assert "Sección" in text
    assert r"\u00f3" not in text
    # sort_keys=True over the top level.
    assert text.index('"entries"') < text.index('"layer"') < text.index('"location_id"')


def test_ms1_entries_are_sorted_ascending_by_location_in_codepoint_order() -> None:
    payload = json.loads(canonical_manifest_bytes(a_manifest()))
    written = [entry["location"] for entry in payload["entries"]]
    assert written == sorted(written)
    assert written == ["sub-0001.pdf", "sub-0002.pdf"]


def test_ms4_the_file_is_written_as_bytes_with_no_carriage_return(tmp_path: Path) -> None:
    """HINT-004. On Windows a default text-mode write emits CRLF and VR-042's
    byte comparison then fails for a line-ending reason unrelated to content."""
    location = tmp_path / "synthetic" / "PRJ-004"
    written = write_manifest(location, a_manifest())
    raw = written.read_bytes()
    assert written.name == "manifest.json"
    assert b"\r" not in raw
    assert raw == canonical_manifest_bytes(a_manifest())


def test_ms4_two_writes_of_one_manifest_are_byte_identical(tmp_path: Path) -> None:
    """VR-042's shape, at the writer: the re-run goes into a second tree, never
    over the first, so the comparison cannot be a file against itself."""
    first = write_manifest(tmp_path / "one" / "PRJ-004", a_manifest())
    second = write_manifest(tmp_path / "two" / "PRJ-004", a_manifest())
    assert first != second
    assert first.read_bytes() == second.read_bytes()


def test_a_manifest_written_into_the_wrong_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError) as raised:
        write_manifest(tmp_path / "PRJ-009", a_manifest())
    assert "PRJ-004" in str(raised.value)


def test_vr058_no_version_revision_generated_at_or_updated_field_is_written() -> None:
    payload = json.loads(canonical_manifest_bytes(a_manifest()))
    forbidden = {"version", "revision", "generated_at", "updated"}
    assert set(payload) & forbidden == set()
    assert set(payload) == {"location_id", "layer", "entries", "project_id"}
    for entry in payload["entries"]:
        assert set(entry) & forbidden == set()


def test_ms5_the_writer_reads_no_clock() -> None:
    """MS-5 as a property of the module rather than a promise about it: a
    `generated_at` or a wall-clock `retrieved_at` would make every re-run
    rewrite every manifest and break VR-042 permanently."""
    text = Path(manifest_module.__file__).read_text(encoding="utf-8")
    for clock in ("datetime.now", "datetime.utcnow", "date.today", "time.time", "time.monotonic"):
        assert clock not in text, f"{clock} appears in the manifest writer"


# --------------------------------------------------------------------------
# The digest helpers — five kinds, kept distinct (FR-007, VR-016)
# --------------------------------------------------------------------------


@given(payload=st.binary(max_size=256))
def test_every_emitted_digest_has_the_declared_form(payload: bytes) -> None:
    assert DIGEST_PATTERN.fullmatch(sha256_of_bytes(payload))


def test_content_hash_is_over_the_committed_bytes_and_upstream_over_the_response(
    tmp_path: Path,
) -> None:
    """The two are computed by one procedure over two *different* inputs, which
    is what makes FR-008a's equality a check rather than a tautology: the
    digest is taken from the response body before the write (FR-008c), and the
    file is digested independently afterwards."""
    body = b"%PDF-1.7\nretrieved bytes\n"
    document = tmp_path / "ufgs-26-05-00.pdf"
    document.write_bytes(body)

    assert upstream_digest_of_response(body) == content_hash_of_file(document)

    document.write_bytes(body + b"tampered")
    assert upstream_digest_of_response(body) != content_hash_of_file(document)


def test_sha256_of_file_matches_the_digest_of_the_same_bytes(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 8
    target = tmp_path / "blob.bin"
    target.write_bytes(payload)
    assert sha256_of_file(target) == sha256_of_bytes(payload)


def test_generation_input_digests_names_exactly_the_closed_three(tmp_path: Path) -> None:
    """VR-061. The keys are validator-owned literals compared before any
    filesystem access, so no manifest-supplied path reaches a resolution step."""
    for relative in GENERATION_INPUT_PATHS:
        target = tmp_path.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(relative.encode("utf-8"))

    digested = generation_input_digests(tmp_path)
    assert set(digested) == set(GENERATION_INPUT_PATHS)
    assert all(DIGEST_PATTERN.fullmatch(value) for value in digested.values())
    assert digested[GENERATION_INPUT_PATHS[0]] == sha256_of_bytes(
        GENERATION_INPUT_PATHS[0].encode("utf-8")
    )


def test_a_missing_generation_input_is_an_error_not_an_omitted_key(tmp_path: Path) -> None:
    with pytest.raises(ManifestError) as raised:
        generation_input_digests(tmp_path)
    assert GENERATION_INPUT_PATHS[0] in str(raised.value)


def test_generation_inputs_must_name_exactly_the_closed_three() -> None:
    with pytest.raises(ManifestError) as raised:
        a_synthetic_entry(
            "sub-0001.pdf",
            generation_inputs={
                **dict.fromkeys(GENERATION_INPUT_PATHS, A_DIGEST),
                "data/roster/project-vendor-roster.json": A_DIGEST,
            },
        )
    assert "unexpected=" in str(raised.value)


def test_roster_hash_is_the_readers_value_and_is_not_recomputed() -> None:
    """FR-020 / VR-029. `Roster.content_hash` is over the roster's canonical
    re-serialized *content*; the file's raw bytes give a different number, and
    recording that one under the same name would make a roster reformat read as
    drift."""
    roster = read_roster()
    entry = a_synthetic_entry("sub-0001.pdf", roster_hash=roster_digest(roster))
    assert entry.roster_hash == roster.content_hash
    assert roster_digest(roster) != sha256_of_file(
        Path(__file__).resolve().parents[3] / "data" / "roster" / "project-vendor-roster.json"
    )


def test_a_digest_in_uppercase_hex_is_rejected() -> None:
    """VR-016: the recorded form must be the one `read_roster()` emits."""
    with pytest.raises(ManifestError):
        a_synthetic_entry("sub-0001.pdf", content_hash="sha256:" + "A" * 64)


@pytest.mark.parametrize(
    "location",
    ["../escape.pdf", "nested/file.pdf", "/absolute.pdf", "C:\\drive.pdf", ".hidden.pdf", "x.txt"],
)
def test_a_location_that_is_not_a_single_pdf_filename_is_rejected(location: str) -> None:
    """VR-009's lexical half, before any resolution: `location` is one
    filename, so a separator, a `..` segment or an absolute prefix fails here
    rather than at a filesystem step."""
    with pytest.raises(ManifestError):
        a_synthetic_entry(location)


@pytest.mark.parametrize("form", ["sección.pdf", "seccíon.pdf"])
def test_a_non_ascii_location_is_refused_in_either_normal_form(form: str) -> None:
    """VR-068's first half, discharged more strongly than by normalizing: the
    `location` pattern VR-009 fixes is ASCII, so a non-ASCII filename is
    refused whichever normal form it arrives in, and the entry↔file match never
    has to decide equivalence between two encodings of one name. NFC
    normalization still runs first, so it is the *normalized* value the
    case-folding comparison sees."""
    with pytest.raises(ManifestError):
        a_synthetic_entry(form)


def test_case_folding_is_compared_over_the_normalized_form() -> None:
    """VR-068's second half, over ASCII case, which is what the pattern admits."""
    with pytest.raises(ManifestError) as raised:
        Manifest(
            location_id="synthetic/PRJ-005",
            entries=(a_synthetic_entry("SUB-0001.pdf"), a_synthetic_entry("sub-0001.pdf")),
            project_id="PRJ-005",
        )
    assert "sub-0001.pdf" in str(raised.value)
