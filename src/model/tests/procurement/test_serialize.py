"""FR-013: the canonical serializer and the record types, on worked cases.

A property tier is not a substitute for a worked case, and `plan.md` says so
where it moves `serialize.py` into the property tier: the properties say the
digest is invariant to key order and to file layout, but nothing in them says
*which* digest. Only a pinned literal does that. If the canonicalization rule
set ever changed — a separator, an escape policy, a trailing newline — every
property here would still hold and every reproducibility claim in the epic would
still be internally consistent, while the committed sidecar became unreachable.
The two literals below are the tripwire for that.

The four cases the task list names are each here by name: a non-ASCII
description, a trailing-zero quantity, an absent `note`, and a CRLF checkout
beside an LF one.

This file also carries the unit cases for `model.py` and `paths.py`, which
`plan.md` § Testing Strategy places in the Unit tier but which no task in
`tasks.md` owns a file for. They live here because the record types exist to be
serialized and their rendering rules — the fixed decimal scale, the `Z` suffix,
`timespec="seconds"` — are digest-affecting: testing them anywhere else would
separate the rule from the thing it protects.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from model.corpus.equipment import EQUIPMENT_MAP_INPUT_PATH
from model.corpus.manifest import DIGEST_PATTERN
from model.procurement.model import (
    DIGEST_KIND_CANONICAL_CONTENT,
    DIGEST_KIND_RAW_BYTES,
    ENVELOPE_KEYS,
    EVENT_KEYS,
    LINE_KEYS,
    NS_E005,
    NS_E005_NAME,
    FixtureEnvelope,
    FixtureEvent,
    FixtureLine,
    GenerationInput,
    LibraryPin,
    LicenseBasis,
    OrderDateWindow,
    ProcurementModelError,
    quantity_string,
    rfc3339_utc,
)
from model.procurement.paths import (
    EMITTED_ARTIFACT_NAMES,
    REPO_ROOT,
    emitted_artifacts,
    fixture_path,
    ground_truth_dir,
    procurement_dir,
    truth_path,
)
from model.procurement.serialize import (
    MAX_NESTING_DEPTH,
    SerializeError,
    canonical_payload_bytes,
    committed_file_bytes,
    dataset_content_hash,
    parse_payload,
    read_payload,
    write_payload,
)
from model.roster.reader import DEFAULT_ROSTER_PATH

# --------------------------------------------------------------------------
# Sample records
# --------------------------------------------------------------------------

#: The two `generation_inputs` paths, **derived rather than written out**.
#:
#: VR-013 and VR-045 hold the roster's filename to exactly one naming site under
#: `src/` — `model/roster/reader.py` — and `tests/checks/test_single_import_site.py`
#: fails the build on a second one. That rule reaches this file: an early draft
#: spelled the path as a literal here and the scan caught it, which is the rule
#: working rather than the rule being inconvenient. The same reasoning applies to
#: the category map, whose path E002 already publishes as
#: `corpus.equipment.EQUIPMENT_MAP_INPUT_PATH`, itself taken from the closed
#: three in `corpus.manifest`. Every later E005 module that records a generation
#: input has the same obligation.
ROSTER_INPUT = DEFAULT_ROSTER_PATH.relative_to(REPO_ROOT).as_posix()
CATEGORY_MAP_INPUT = EQUIPMENT_MAP_INPUT_PATH

#: A description that leaves ASCII in three different ways: a combining-free
#: accented letter, an em dash, and the numero sign.
NON_ASCII_DESCRIPTION = "Refroidisseur d'eau — sous-station n° 3"


def an_event(sequence_no: int = 1, to_state: str = "submitted", day: int = 16) -> FixtureEvent:
    return FixtureEvent(
        sequence_no=sequence_no,
        to_state=to_state,
        occurred_at=datetime(2025, 6, day, tzinfo=UTC),
    )


def a_line(**overrides: Any) -> FixtureLine:
    defaults: dict[str, Any] = {
        "project_id": "PRJ-001",
        "vendor_id": "VND-001",
        "po_number": "PO-001-0001",
        "line_number": 1,
        "material_category": "WATER_CHILLER",
        "description": NON_ASCII_DESCRIPTION,
        "manufacturer": "Ironvane Thermal",
        "part_number": "IRV-236500-0001",
        "quantity": Decimal("6"),
        "unit_of_measure": "EA",
        "order_date": date(2025, 6, 16),
        "need_by_date": date(2025, 9, 30),
        "criticality": 5,
        "events": (an_event(1, "submitted", 16), an_event(2, "under_review", 24)),
    }
    return FixtureLine(**{**defaults, **overrides})


def an_envelope(**overrides: Any) -> FixtureEnvelope:
    defaults: dict[str, Any] = {
        "dataset_schema_version": 1,
        "layer": "SYNTHETIC",
        "generator_id": "model.procurement.generate",
        "generator_revision": 1,
        "root_seed": 20260726,
        "seed_derivation": "SeedSequence(entropy=root_seed, spawn_key=(line_stream_key,))",
        "generation_date": date(2026, 7, 26),
        "as_of_date": date(2026, 4, 1),
        "order_date_window": OrderDateWindow(date(2025, 6, 16), date(2026, 2, 16)),
        "generation_inputs": (
            GenerationInput(ROSTER_INPUT, "sha256:" + "a" * 64, DIGEST_KIND_CANONICAL_CONTENT),
            GenerationInput(CATEGORY_MAP_INPUT, "sha256:" + "b" * 64, DIGEST_KIND_RAW_BYTES),
        ),
        "library_pin": LibraryPin(numpy="2.4.6"),
        "license_basis": LicenseBasis(statement="Generated by this project from a committed seed."),
        "lines": (a_line(),),
    }
    return FixtureEnvelope(**{**defaults, **overrides})


# --------------------------------------------------------------------------
# The pinned worked cases
# --------------------------------------------------------------------------

#: Four rules in one byte string: keys sorted, separators compact, non-ASCII
#: emitted as UTF-8 rather than escaped, no trailing newline. Written out as
#: bytes rather than derived, because a derivation would move with the code.
WORKED_PAYLOAD: dict[str, Any] = {"b": 1, "a": {"d": "café", "c": [1, None, True]}}
WORKED_CANONICAL = b'{"a":{"c":[1,null,true],"d":"caf\xc3\xa9"},"b":1}'
WORKED_DIGEST = "sha256:d0cab65a35dc7447b00e2aeae2738c2cc2f5a7ab6df09d08cd81393a406ed620"

#: The digest of the one-line envelope built above. It pins the record types'
#: rendering as well as the serializer's: change how a date, an instant or a
#: quantity is written and this number moves.
ENVELOPE_DIGEST = "sha256:a0d9a80a3033dac4c1c1384edf620ce0c12491cf3a23f4a4f076ebc7c38dc48d"


def test_canonical_bytes_are_the_pinned_rule_set() -> None:
    assert canonical_payload_bytes(WORKED_PAYLOAD) == WORKED_CANONICAL
    assert not WORKED_CANONICAL.endswith(b"\n")
    assert b"\\u00e9" not in WORKED_CANONICAL


def test_the_worked_digest_is_pinned() -> None:
    assert dataset_content_hash(WORKED_PAYLOAD) == WORKED_DIGEST
    assert DIGEST_PATTERN.fullmatch(WORKED_DIGEST)


def test_the_envelope_digest_is_pinned() -> None:
    assert dataset_content_hash(an_envelope().to_payload()) == ENVELOPE_DIGEST


# --------------------------------------------------------------------------
# Worked case: a non-ASCII description
# --------------------------------------------------------------------------


def test_a_non_ascii_description_is_utf8_not_escaped() -> None:
    payload = an_envelope().to_payload()
    raw = canonical_payload_bytes(payload)

    assert NON_ASCII_DESCRIPTION.encode("utf-8") in raw
    assert b"\\u2014" not in raw  # the em dash, had ensure_ascii been left on
    assert parse_payload(raw)["lines"][0]["description"] == NON_ASCII_DESCRIPTION


# --------------------------------------------------------------------------
# Worked case: the trailing zero on `quantity`
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        (Decimal("6"), "6.0"),
        (Decimal("6.0"), "6.0"),
        (Decimal("6.00"), "6.0"),
        (Decimal("0.5"), "0.5"),
        (Decimal("12.50"), "12.5"),
        (Decimal("480.0"), "480.0"),
    ],
)
def test_quantity_is_written_at_a_fixed_scale_of_one(value: Decimal, rendered: str) -> None:
    """`6.0` and never `6` or `6.00`, whichever `Decimal` the generator hands over."""
    assert quantity_string(value) == rendered
    assert a_line(quantity=value).to_payload()["quantity"] == rendered


def test_a_trailing_zero_is_content_here_even_though_numeric_ignores_it() -> None:
    """HINT-005, as a worked case rather than a comment.

    In SQL `numeric`, `12.50 = 12.5` is true. In the digest they are two
    different payloads. That is exactly why the scale is *fixed* rather than
    bounded: if the fixture were free to write either form, the loader's
    comparison and the reproducibility oracle could disagree about one value.
    """
    at_scale_one = dataset_content_hash({"quantity": "12.5"})
    at_scale_two = dataset_content_hash({"quantity": "12.50"})

    assert at_scale_one != at_scale_two
    assert quantity_string(Decimal("12.50")) == "12.5"


def test_a_quantity_needing_two_decimals_is_refused_rather_than_rounded() -> None:
    with pytest.raises(ProcurementModelError, match="not exactly representable"):
        quantity_string(Decimal("6.25"))


@pytest.mark.parametrize("value", [6.0, "6.0", 6])
def test_a_quantity_that_is_not_a_decimal_is_refused(value: object) -> None:
    with pytest.raises(ProcurementModelError, match="must be a Decimal"):
        quantity_string(value)  # type: ignore[arg-type]


def test_a_non_finite_quantity_is_refused() -> None:
    with pytest.raises(ProcurementModelError, match="must be finite"):
        quantity_string(Decimal("NaN"))


# --------------------------------------------------------------------------
# Worked case: `note` absent, not null
# --------------------------------------------------------------------------


def test_the_event_record_emits_no_note_key_at_all() -> None:
    """DV-022. Absent and null are two different payloads and two different digests."""
    event = an_event().to_payload()
    assert set(event) == {"sequence_no", "to_state", "occurred_at"}
    assert "note" not in event


def test_an_absent_note_and_an_explicit_null_are_different_digests() -> None:
    without = {"sequence_no": 1, "to_state": "submitted"}
    with_null = {"sequence_no": 1, "to_state": "submitted", "note": None}

    assert dataset_content_hash(without) != dataset_content_hash(with_null)


# --------------------------------------------------------------------------
# Worked case: a CRLF checkout beside an LF one
# --------------------------------------------------------------------------


def test_the_digest_is_the_same_on_a_crlf_and_an_lf_checkout(tmp_path: Path) -> None:
    """The whole reason the digest covers parsed content rather than file bytes.

    `core.autocrlf` is true on the Windows development machine and the Linux
    verification runner is the platform of record. Both files below are what one
    checkout of the same commit looks like, and the oracle has to give them the
    same answer.
    """
    payload = an_envelope().to_payload()

    lf_file = write_payload(tmp_path / "lf" / "procurement-history.json", payload)
    lf_bytes = lf_file.read_bytes()
    crlf_file = tmp_path / "crlf" / "procurement-history.json"
    crlf_file.parent.mkdir(parents=True)
    crlf_file.write_bytes(lf_bytes.replace(b"\n", b"\r\n"))

    assert b"\r\n" not in lf_bytes  # write_bytes, never text mode
    assert crlf_file.read_bytes() != lf_bytes

    assert dataset_content_hash(read_payload(lf_file)) == ENVELOPE_DIGEST
    assert dataset_content_hash(read_payload(crlf_file)) == ENVELOPE_DIGEST


def test_the_committed_file_is_indented_sorted_and_ends_with_one_newline() -> None:
    raw = committed_file_bytes(an_envelope().to_payload())
    text = raw.decode("utf-8")

    assert text.endswith("}\n")
    assert not text.endswith("}\n\n")
    assert "\n  " in text  # indent=2
    assert text.index('"as_of_date"') < text.index('"dataset_schema_version"')  # sort_keys


def test_a_written_payload_reads_back_equal(tmp_path: Path) -> None:
    payload = an_envelope().to_payload()
    written = write_payload(tmp_path / "nested" / "deeper" / "history.json", payload)

    assert written.exists()
    assert read_payload(written) == payload


def test_reading_a_missing_payload_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(SerializeError, match="unreadable"):
        read_payload(tmp_path / "absent.json")


# --------------------------------------------------------------------------
# The refusals: the serializer must be able to say no
# --------------------------------------------------------------------------


def test_a_float_is_named_by_its_location() -> None:
    with pytest.raises(SerializeError, match=r"\$\.lines\[1\]\.quantity is a float"):
        canonical_payload_bytes({"lines": [{"quantity": "1.0"}, {"quantity": 2.5}]})


def test_a_tuple_is_refused_even_though_json_would_write_it() -> None:
    """A tuple in and a list out is a round-trip failure on a value that serializes."""
    with pytest.raises(SerializeError, match="is a tuple"):
        canonical_payload_bytes({"lines": (1, 2)})


def test_a_non_string_object_key_is_refused_rather_than_coerced() -> None:
    with pytest.raises(SerializeError, match="int key"):
        canonical_payload_bytes({"lines": {1: "one"}})


def test_a_payload_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(SerializeError, match="one JSON object"):
        canonical_payload_bytes([1, 2, 3])  # type: ignore[arg-type]


def test_a_payload_nested_past_the_cap_is_refused_rather_than_hanging() -> None:
    deep: dict[str, Any] = {"leaf": 1}
    for _ in range(MAX_NESTING_DEPTH + 2):
        deep = {"level": deep}

    with pytest.raises(SerializeError, match="nests deeper"):
        canonical_payload_bytes(deep)


def test_a_self_referencing_payload_raises_instead_of_looping() -> None:
    payload: dict[str, Any] = {}
    payload["self"] = payload

    with pytest.raises(SerializeError, match="nests deeper"):
        canonical_payload_bytes(payload)


def test_a_byte_order_mark_is_a_read_failure() -> None:
    with pytest.raises(SerializeError, match="byte-order mark"):
        parse_payload(b"\xef\xbb\xbf" + b'{"a":1}')


def test_a_bare_json_float_in_a_committed_file_is_refused_on_the_way_in() -> None:
    """A hand-edited fixture does not silently become the oracle's input."""
    with pytest.raises(SerializeError, match="parses as a float"):
        parse_payload(b'{"quantity": 12.5}')


@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
def test_the_non_standard_json_tokens_are_refused(token: bytes) -> None:
    with pytest.raises(SerializeError, match="non-standard JSON token"):
        parse_payload(b'{"quantity": ' + token + b"}")


def test_invalid_utf8_json_is_refused() -> None:
    with pytest.raises(SerializeError, match="not valid UTF-8 JSON"):
        parse_payload(b'{"a": ')


def test_parsing_something_other_than_bytes_is_refused() -> None:
    with pytest.raises(SerializeError, match="parsed from bytes"):
        parse_payload('{"a":1}')  # type: ignore[arg-type]


def test_a_parsed_json_array_is_not_a_payload() -> None:
    with pytest.raises(SerializeError, match="one JSON object"):
        parse_payload(b"[1,2,3]")


# --------------------------------------------------------------------------
# The record types' rendering rules
# --------------------------------------------------------------------------


def test_an_instant_renders_with_a_literal_z_at_second_precision() -> None:
    assert rfc3339_utc(datetime(2026, 4, 1, tzinfo=UTC)) == "2026-04-01T00:00:00Z"


def test_a_naive_instant_is_refused_rather_than_assumed_to_be_utc() -> None:
    with pytest.raises(ProcurementModelError, match="is naive"):
        rfc3339_utc(datetime(2026, 4, 1))  # noqa: DTZ001 - the point of the test


def test_an_offset_instant_is_refused_rather_than_normalized() -> None:
    with pytest.raises(ProcurementModelError, match="carries offset"):
        rfc3339_utc(datetime(2026, 4, 1, tzinfo=timezone(timedelta(hours=2))))


def test_a_time_of_day_is_refused_as_invented_precision() -> None:
    with pytest.raises(ProcurementModelError, match="time of day"):
        rfc3339_utc(datetime(2026, 4, 1, 9, 30, tzinfo=UTC))


def test_something_that_is_not_a_datetime_is_refused() -> None:
    with pytest.raises(ProcurementModelError, match="must be a datetime"):
        rfc3339_utc(date(2026, 4, 1))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "digest",
    ["sha256:" + "A" * 64, "sha256:" + "a" * 63, "a" * 64, "", "sha256:"],
)
def test_a_generation_input_refuses_a_malformed_digest(digest: str) -> None:
    with pytest.raises(ProcurementModelError, match="lowercase hex"):
        GenerationInput(ROSTER_INPUT, digest, DIGEST_KIND_CANONICAL_CONTENT)


def test_a_generation_input_refuses_an_unknown_digest_kind() -> None:
    with pytest.raises(ProcurementModelError, match="digest_kind"):
        GenerationInput(ROSTER_INPUT, "sha256:" + "a" * 64, "file_bytes")


def test_a_generation_input_needs_a_path() -> None:
    with pytest.raises(ProcurementModelError, match="repository-relative path"):
        GenerationInput("   ", "sha256:" + "a" * 64, DIGEST_KIND_RAW_BYTES)


def test_the_two_generation_inputs_record_their_own_conventions() -> None:
    """AD-010 / G-3: per-owner, and the artifact says which is which."""
    roster, category_map = an_envelope().to_payload()["generation_inputs"]

    assert roster["path"] == ROSTER_INPUT
    assert roster["digest_kind"] == DIGEST_KIND_CANONICAL_CONTENT
    assert category_map["path"] == CATEGORY_MAP_INPUT
    assert category_map["digest_kind"] == DIGEST_KIND_RAW_BYTES


# --------------------------------------------------------------------------
# The closed field sets, and the namespace
# --------------------------------------------------------------------------


def test_the_envelope_carries_exactly_thirteen_keys() -> None:
    payload = an_envelope().to_payload()
    assert set(payload) == set(ENVELOPE_KEYS)
    assert len(ENVELOPE_KEYS) == 13
    assert "roster_hash" not in payload  # carried once, in generation_inputs


def test_the_line_carries_exactly_fourteen_keys_and_no_derived_value() -> None:
    payload = a_line().to_payload()
    assert set(payload) == set(LINE_KEYS)
    assert len(LINE_KEYS) == 14
    for derived in ("po_line_id", "lifecycle_state", "is_closed", "closing_event_id"):
        assert derived not in payload


def test_the_event_carries_exactly_three_keys_and_no_derived_value() -> None:
    payload = an_event().to_payload()
    assert set(payload) == set(EVENT_KEYS)
    assert len(EVENT_KEYS) == 3
    for derived in ("event_id", "from_state", "is_terminal", "prev_sequence_no"):
        assert derived not in payload


def test_the_namespace_is_recomputable_rather_than_a_magic_constant() -> None:
    """`data-model.md` §Name construction pins the value; this shows where it comes from."""
    assert uuid.uuid5(uuid.NAMESPACE_URL, NS_E005_NAME) == NS_E005
    assert str(NS_E005) == "6a5c9561-8a6b-58f7-8fbd-db51856db549"


def test_the_natural_key_is_the_key_everything_downstream_joins_on() -> None:
    assert a_line().natural_key == ("PRJ-001", "PO-001-0001", 1)


# --------------------------------------------------------------------------
# Path resolution
# --------------------------------------------------------------------------


def test_the_artifacts_resolve_under_this_checkout() -> None:
    assert fixture_path().parent == procurement_dir()
    assert truth_path().parent == ground_truth_dir()
    assert fixture_path().relative_to(REPO_ROOT).as_posix() == EMITTED_ARTIFACT_NAMES[0]


def test_the_ground_truth_record_is_not_in_the_fixture_directory() -> None:
    """FR-018 / AD-007: separation is the requirement, not a preference."""
    assert ground_truth_dir() != procurement_dir()
    assert not ground_truth_dir().is_relative_to(procurement_dir())


def test_the_emitted_set_is_exactly_four_files_in_a_fixed_order(tmp_path: Path) -> None:
    """DV-020 is stated positively so it is checkable; a tuple, so it is ordered."""
    artifacts = emitted_artifacts(tmp_path)

    assert len(artifacts) == 4
    assert len(set(artifacts)) == 4
    assert [p.relative_to(tmp_path).as_posix() for p in artifacts] == list(EMITTED_ARTIFACT_NAMES)


def test_a_tmp_root_moves_every_artifact_together(tmp_path: Path) -> None:
    for artifact in emitted_artifacts(tmp_path):
        assert artifact.is_relative_to(tmp_path)
