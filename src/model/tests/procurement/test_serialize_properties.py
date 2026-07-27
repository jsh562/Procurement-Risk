"""FR-013 / FR-021: the canonical serializer, at the property tier.

`plan.md` § Testing Strategy admits `serialize.py` to the **mandatory** property
tier and says why: this is the oracle FR-021 and SC-012 rest on, and a
serializer that is merely *usually* canonical makes every reproducibility claim
in the epic unfalsifiable. A non-canonical digest is still sixty-four hexadecimal
characters, so nothing downstream refuses it — clause 3 of the admission rule,
"wrong is silent", in its purest form.

Three relations are asserted here, each named in § Mandated properties with its
own input domain:

* **Round-trip** — `parse(canonical_bytes(x))` equals `x`. Domain: non-ASCII in
  `description`, `quantity` carrying a trailing zero, `note` absent rather than
  null.
* **Metamorphic, key order** — `canonical_bytes` is invariant to the order the
  keys were inserted in. Domain: every object in the payload independently
  shuffled. This is the property that makes the digest a function of *content*;
  without it, two runs that built the same payload by different code paths would
  hash differently.
* **Metamorphic, file layout** — the digest is invariant to the committed file's
  layout. Domain: indented and compact files, CRLF and LF checkouts. This is the
  property AD-001 buys by hashing re-serialized *parsed* content instead of file
  bytes, and it is the reason a Windows checkout and the Linux verification
  runner agree.

Two controls sit beside them, because a serializer that never refuses anything
is indistinguishable from one whose checks do nothing: a payload carrying a JSON
float must be refused (AD-004 removes float repr from the oracle entirely), and
two payloads differing anywhere must not collide.

The Hypothesis budget — 200 examples, derandomized, no deadline — comes from
`tests/conftest.py` and is not overridden here.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from model.corpus.manifest import DIGEST_PATTERN
from model.procurement.serialize import (
    SerializeError,
    canonical_payload_bytes,
    committed_file_bytes,
    dataset_content_hash,
    parse_payload,
)

# --------------------------------------------------------------------------
# Strategies
# --------------------------------------------------------------------------

#: Everything JSON admits **except** floats. The exclusion is the point rather
#: than a convenience: `data-model.md` §Conventions bans a JSON float from the
#: fixture outright, so a float is an input this serializer must refuse, and
#: generating one inside the "valid payload" strategy would make the round-trip
#: property assert the opposite of the requirement.
scalars = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-(10**12), max_value=10**12)
    | st.text(max_size=40)
)

#: Arbitrary nested payloads. Object keys are non-empty text so that a shuffle
#: has something to shuffle, and the nesting is shallow because the properties
#: under test are structural — a deeper tree exercises `json` rather than this
#: module.
payloads = st.dictionaries(
    keys=st.text(min_size=1, max_size=20),
    values=st.recursive(
        scalars,
        lambda children: (
            st.lists(children, max_size=4)
            | st.dictionaries(st.text(min_size=1, max_size=20), children, max_size=4)
        ),
        max_leaves=12,
    ),
    min_size=1,
    max_size=6,
)

#: Descriptions that reach beyond ASCII. `ensure_ascii=False` is part of the
#: canonical rule set, so a non-ASCII description is encoded as UTF-8 rather
#: than escaped, and the round-trip has to survive that.
descriptions = st.one_of(
    st.just("Water Chiller (Tag 201-14)"),
    st.just("Refroidisseur d'eau — sous-station n° 3"),
    st.just("冷水机组"),
    st.text(min_size=1, max_size=30),
)

#: `quantity` is always a decimal **string** at a fixed scale of exactly one, so
#: the trailing zero of `6.0` is content and must survive the round-trip. In SQL
#: `numeric` it would not: `12.50 = 12.5` there while the two are different
#: digests here (AD-004, HINT-005).
quantity_strings = st.one_of(
    st.just("6.0"),
    st.just("0.5"),
    st.just("480.0"),
    st.integers(min_value=1, max_value=4800).map(lambda tenths: f"{tenths // 10}.{tenths % 10}"),
)


@st.composite
def fixture_like_payloads(draw: st.DrawFn) -> dict[str, Any]:
    """A payload shaped like the real fixture, over the declared input domain.

    Not the real generator's output — that does not exist yet, and a property
    test that could only run after the thing it gates would gate nothing. What
    it reproduces is the *shape* the domain column of § Mandated properties
    names: nested `lines[].events[]`, a non-ASCII description, a fixed-scale
    quantity string, and an optional `note` that is **absent** rather than null
    when it is not there.
    """
    line_count = draw(st.integers(min_value=1, max_value=3))
    lines = []
    for index in range(line_count):
        events = []
        for sequence_no in range(1, draw(st.integers(min_value=1, max_value=3)) + 1):
            event: dict[str, Any] = {
                "sequence_no": sequence_no,
                "to_state": draw(st.sampled_from(("submitted", "under_review", "delivered"))),
                "occurred_at": f"2025-06-{sequence_no + 15:02d}T00:00:00Z",
            }
            if draw(st.booleans()):
                event["note"] = None
            events.append(event)
        lines.append(
            {
                "project_id": f"PRJ-00{index + 1}",
                "vendor_id": "VND-001",
                "po_number": f"PO-00{index + 1}-0001",
                "line_number": 1,
                "material_category": "WATER_CHILLER",
                "description": draw(descriptions),
                "manufacturer": "Ironvane Thermal",
                "part_number": "IRV-236500-0001",
                "quantity": draw(quantity_strings),
                "unit_of_measure": "EA",
                "order_date": "2025-06-16",
                "need_by_date": "2025-09-30",
                "criticality": draw(st.integers(min_value=1, max_value=5)),
                "events": events,
            }
        )
    return {
        "dataset_schema_version": 1,
        "layer": "SYNTHETIC",
        "as_of_date": "2026-04-01",
        "root_seed": draw(st.integers(min_value=0, max_value=2**31)),
        "lines": lines,
    }


def reorder(value: Any, data: st.DataObject) -> Any:
    """Rebuild `value` with every object's keys in a drawn permutation.

    Lists keep their order, deliberately: for a JSON array, order *is* content —
    `lines[]` is sorted by natural key and `events[]` by `sequence_no` — so
    permuting one would assert the opposite of DV-023.
    """
    if isinstance(value, dict):
        items = [(key, reorder(item, data)) for key, item in value.items()]
        return dict(data.draw(st.permutations(items)))
    if isinstance(value, list):
        return [reorder(item, data) for item in value]
    return value


# --------------------------------------------------------------------------
# Round-trip
# --------------------------------------------------------------------------


@given(payload=payloads)
def test_parse_of_canonical_bytes_returns_the_payload(payload: dict[str, Any]) -> None:
    assert parse_payload(canonical_payload_bytes(payload)) == payload


@given(payload=fixture_like_payloads())
def test_round_trip_over_the_declared_input_domain(payload: dict[str, Any]) -> None:
    """Non-ASCII descriptions, trailing-zero quantities and an absent `note`."""
    recovered = parse_payload(canonical_payload_bytes(payload))
    assert recovered == payload
    for original, restored in zip(payload["lines"], recovered["lines"], strict=True):
        assert restored["description"] == original["description"]
        assert restored["quantity"] == original["quantity"]
        for before, after in zip(original["events"], restored["events"], strict=True):
            # Absent is not null. A `note` key that appears from nowhere is a
            # different dataset, and DV-022 requires it absent on every event.
            assert ("note" in after) == ("note" in before)


# --------------------------------------------------------------------------
# Metamorphic: key order
# --------------------------------------------------------------------------


@given(payload=payloads, data=st.data())
def test_canonical_bytes_ignore_the_order_keys_were_inserted_in(
    payload: dict[str, Any], data: st.DataObject
) -> None:
    shuffled = reorder(payload, data)
    assert canonical_payload_bytes(shuffled) == canonical_payload_bytes(payload)
    assert dataset_content_hash(shuffled) == dataset_content_hash(payload)


@given(payload=fixture_like_payloads(), data=st.data())
def test_the_digest_ignores_key_order_in_a_fixture_shaped_payload(
    payload: dict[str, Any], data: st.DataObject
) -> None:
    assert dataset_content_hash(reorder(payload, data)) == dataset_content_hash(payload)


# --------------------------------------------------------------------------
# Metamorphic: committed file layout
# --------------------------------------------------------------------------


@given(payload=fixture_like_payloads())
def test_the_digest_survives_the_committed_file_layout(payload: dict[str, Any]) -> None:
    """Indented-on-disk and compact-in-memory are two byte strings, one digest.

    `data-model.md` calls this out as "not a contradiction": the canonical form
    carries no trailing newline and the committed file carries exactly one.
    Neither value may be changed to match the other, because the difference is
    the mechanism — a digest over file bytes would move under git end-of-line
    normalisation and a digest over parsed content cannot.
    """
    on_disk = committed_file_bytes(payload)
    canonical = canonical_payload_bytes(payload)

    assert on_disk.endswith(b"\n")
    assert not canonical.endswith(b"\n")
    assert on_disk != canonical
    assert dataset_content_hash(parse_payload(on_disk)) == dataset_content_hash(payload)


@given(payload=fixture_like_payloads())
def test_the_digest_survives_a_crlf_checkout(payload: dict[str, Any]) -> None:
    """The Windows development machine and the Linux runner must agree.

    `core.autocrlf` rewrites `\\n` to `\\r\\n` in the working copy. That changes
    the file's bytes and must not change the digest; `.gitattributes` pins these
    artifacts to `eol=lf` as well, so this is the second of two independent
    defences rather than the only one.
    """
    lf_bytes = committed_file_bytes(payload)
    crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")

    assert crlf_bytes != lf_bytes
    assert dataset_content_hash(parse_payload(crlf_bytes)) == dataset_content_hash(
        parse_payload(lf_bytes)
    )


# --------------------------------------------------------------------------
# Digest surface, and the two controls
# --------------------------------------------------------------------------


@given(payload=payloads)
def test_the_digest_has_the_one_published_surface_form(payload: dict[str, Any]) -> None:
    assert DIGEST_PATTERN.fullmatch(dataset_content_hash(payload))


@given(payload=payloads, key=st.text(min_size=1, max_size=8), value=st.integers())
def test_a_changed_payload_changes_the_digest(
    payload: dict[str, Any], key: str, value: int
) -> None:
    """The oracle can fail. Without this, every reproduction claim is vacuous."""
    mutated = dict(payload)
    mutated[key] = value
    if mutated == payload:
        return
    assert dataset_content_hash(mutated) != dataset_content_hash(payload)


@given(number=st.floats(allow_nan=True, allow_infinity=True))
def test_a_json_float_anywhere_is_refused(number: float) -> None:
    """AD-004 removes float repr from the oracle, so a float is an error here.

    `round()` on a binary float is not a canonical decimal — `round(2.675, 2)`
    is `2.67` — and `json.dumps` emits the non-standard `NaN` and `Infinity`
    tokens unless told not to. Refusing the type outright is one rule instead of
    three, and it is checkable.
    """
    with pytest.raises(SerializeError):
        canonical_payload_bytes({"lines": [{"quantity": number}]})
