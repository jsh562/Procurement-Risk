"""TR-019 / TR-020 / TR-038: the fixture key, and what it is a hash of.

Written before `compute/hashing.py`, per the test-first rule for deterministic
computation.

**What a fixture key has to be true of.** Two requests that would produce the
same call must produce the same key, or replay misses on a request it has a
fixture for. Two requests that would produce a *different* call must produce a
different key, or replay serves a fixture recorded for something else — which
is the failure that looks like everything working. Both directions get
properties below.

**The closed field list is the interesting half** (TR-020). "Every sampling
parameter" is a category, not an enumeration, so the closure rule is stated the
other way round: the hashed set is every field the gateway's own request model
declares, and a parameter the model does not declare is not passed through — it
fails at request construction rather than being hashed or dropped. Adding a
parameter therefore means adding a field, which a check can see. A request field
that reached the provider without reaching the hash is the defect this file
exists to make impossible.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict, Field

from gateway.compute.hashing import (
    FIXTURE_KEY_PATTERN,
    HASH_ALGORITHM,
    UNHASHED_REQUEST_FIELDS,
    canonical_json,
    fixture_key,
    hashed_request_payload,
    prompt_template_version,
    repair_fixture_key,
    schema_version,
)
from gateway.models import InvocationRequest, generate_trace_id

#: JSON-representable values, nested. Deliberately includes the shapes where a
#: naive serializer differs from a canonical one — dict ordering, float/int
#: boundaries, and non-ASCII text.
json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(10**12), max_value=10**12)
    | st.text(max_size=40),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=12), children, max_size=4)
    ),
    max_leaves=12,
)


# --- TR-019: canonical serialization ----------------------------------------


@given(st.dictionaries(st.text(max_size=12), json_values, max_size=6))
@settings(max_examples=300)
def test_key_order_does_not_change_the_serialization(payload: dict[str, object]) -> None:
    """The property "canonical" means. Two dictionaries with the same items in a
    different insertion order are the same request, and a serializer that
    preserved insertion order would give them different fixture keys — so a
    replay would miss on a request it has a fixture for, for a reason nobody
    could see in the data."""
    reordered = dict(reversed(list(payload.items())))
    assert canonical_json(payload) == canonical_json(reordered)


@given(st.dictionaries(st.text(max_size=12), json_values, max_size=6))
@settings(max_examples=200)
def test_the_serialization_round_trips(payload: dict[str, object]) -> None:
    """Canonical, not lossy. A serializer that dropped or coerced a value would
    hash two different requests to one key — the direction that serves a
    fixture recorded for something else."""
    assert json.loads(canonical_json(payload)) == payload


@given(json_values)
@settings(max_examples=200)
def test_the_serialization_is_deterministic(value: object) -> None:
    assert canonical_json(value) == canonical_json(value)


def test_non_ascii_text_is_not_escaped_away() -> None:
    """`ensure_ascii=True` would encode the same text differently depending on
    nothing the caller controls, and the corpus this project runs on is not
    guaranteed ASCII."""
    assert canonical_json({"k": "café"}) == '{"k":"café"}'


def test_the_serialization_has_no_incidental_whitespace() -> None:
    """Separators are pinned rather than left to the default, which inserts a
    space after each separator — a formatting choice that would silently change
    every key if a future default changed."""
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_an_unserializable_value_fails_rather_than_being_coerced() -> None:
    """TR-020's spirit: fail rather than silently ignore. A value the canonical
    form cannot represent must not be hashed as its `repr` — two distinct
    objects would then share a key."""
    with pytest.raises(TypeError):
        canonical_json({"k": object()})


# --- TR-019 / TR-020: the fixture key ---------------------------------------


class Request(BaseModel):
    """Stands in for the gateway's request model: the hashed set *is* its
    declared fields (TR-020).

    `extra="forbid"` is not decoration on a test double — it is the mechanism
    TR-020's closure rests on, so a stand-in without it would be standing in for
    something that cannot satisfy the requirement. The first draft omitted it
    and the extra field was silently dropped, which is the exact defect the
    requirement names. The real model is asserted to carry it below.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    prompt: str
    temperature: float = 0.0
    top_p: float | None = None


def test_the_key_is_a_labelled_digest() -> None:
    """The stored form is `sha256:<64 hex>`, matched by the column's `CHECK`.

    Labelled rather than bare so the algorithm is readable off any stored key —
    a bare digest would make a future algorithm change indistinguishable from a
    corrupted value.
    """
    key = fixture_key(Request(provider="p", model="m", prompt="hello"))
    assert FIXTURE_KEY_PATTERN.match(key), key
    assert key.startswith(f"{HASH_ALGORITHM}:")


def test_the_same_request_yields_the_same_key() -> None:
    first = Request(provider="p", model="m", prompt="hello", temperature=0.5)
    second = Request(provider="p", model="m", prompt="hello", temperature=0.5)
    assert fixture_key(first) == fixture_key(second)


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "other"},
        {"model": "claude-sonnet-5"},
        {"prompt": "hello "},
        {"temperature": 0.6},
        {"top_p": 0.9},
    ],
)
def test_changing_any_hashed_field_changes_the_key(change: dict[str, object]) -> None:
    """Every declared field is in the hash, including the sampling parameters.

    Parametrized over each field rather than asserted in bulk, so a failure
    names *which* field stopped affecting the key — a field silently dropped
    from the hash means replay serves a fixture recorded under different
    sampling settings, which produces plausible output for the wrong request.
    """
    base = Request(provider="p", model="m", prompt="hello")
    changed = Request(**{**base.model_dump(), **change})
    assert fixture_key(base) != fixture_key(changed), (
        f"changing {sorted(change)} did not change the fixture key"
    )


def test_the_hashed_set_is_exactly_the_declared_fields() -> None:
    """TR-020's closure, stated as the rule rather than as a list.

    The hashed set is derived from the model's own declared fields, so adding a
    parameter *is* adding a field — and a check that compared against a
    hand-kept list would need updating in two places, with nothing comparing
    them.
    """
    request = Request(provider="p", model="m", prompt="x")
    payload = json.loads(canonical_json(json.loads(request.model_dump_json())))
    assert set(payload) == set(Request.model_fields)


def test_the_real_request_model_forbids_undeclared_fields() -> None:
    """The load-bearing half, asserted against the model that ships.

    Every property above uses a stand-in, and a stand-in can be given whatever
    configuration makes the test pass. TR-020's closure rests on the *gateway's*
    request model refusing a field it does not declare — without that, an
    undeclared parameter is dropped, the gateway sends a different request from
    the one it hashed, and the fixture key describes a call that was never made.
    """
    assert InvocationRequest.model_config.get("extra") == "forbid", (
        "InvocationRequest does not forbid extra fields, so an undeclared "
        "provider parameter would be silently dropped rather than refused "
        "(TR-020)"
    )
    with pytest.raises(Exception, match="extra"):
        InvocationRequest(prompt="x", presence_penalty=0.2)  # type: ignore[call-arg]


def test_a_field_the_model_does_not_declare_is_refused() -> None:
    """TR-020: fail rather than silently ignore.

    A provider parameter the request model does not declare is not passed
    through, hashed, or dropped — it fails at construction. Dropping it would
    mean the gateway sent a different request from the one it hashed, so the
    fixture key would describe a call that was never made.
    """
    with pytest.raises(Exception, match="extra"):
        Request(provider="p", model="m", prompt="x", presence_penalty=0.2)  # type: ignore[call-arg]


# --- The correlation identifier is not a semantic input ----------------------
#
# TR-020's closure is stated over the request model's declared fields, and
# `trace_id` is one of them. It is also the one declared field that describes
# *this run* rather than *this call*: FR-070 obliges one run-scoped identifier
# per run, so a caller that supplies one keys the same request differently on
# every run and no recorded fixture can ever be replayed.
#
# E004's own tests never saw it. `resolve_trace_id` mints an identifier when the
# caller supplies none, so every test in this epic keys with `trace_id` null and
# is perfectly stable. The defect lives at the seam with the first caller
# obliged to supply one, which is why these assertions are written against the
# real `InvocationRequest` rather than the stand-in above — the stand-in has no
# correlation identifier to be wrong about.

#: 32 lowercase hex, inside TR-047's domain and not all zero. Spelled out rather
#: than generated, so a failure names two fixed values instead of two that
#: differ on every run — which is the very property under test.
A_TRACE_ID = "a" * 32
ANOTHER_TRACE_ID = "b" * 32


def test_two_requests_differing_only_in_trace_id_share_one_key() -> None:
    """The property FR-070 needs and TR-019 has to keep.

    A correlation identifier says which run observed the call. It does not
    change the prompt, the model, the schema or any sampling parameter, so two
    requests differing only in it would produce the *same* provider call — and
    TR-019's first direction requires the same call to hash the same, or replay
    misses on a request it holds a fixture for.
    """
    untraced = InvocationRequest(prompt="hello", model="claude-opus-5")
    one = InvocationRequest(prompt="hello", model="claude-opus-5", trace_id=A_TRACE_ID)
    two = InvocationRequest(prompt="hello", model="claude-opus-5", trace_id=ANOTHER_TRACE_ID)

    assert fixture_key(one) == fixture_key(two), (
        "two requests differing only in trace_id key differently, so a run-scoped "
        "identifier makes every recorded fixture unreplayable"
    )
    assert fixture_key(one) == fixture_key(untraced), (
        "supplying a trace_id keys differently from omitting one, so a fixture "
        "recorded by a caller that supplies none can never be replayed by one "
        "that does"
    )


def test_the_key_is_stable_across_freshly_minted_trace_ids() -> None:
    """The measurement that found this, as an assertion.

    Six runs of one document produced six distinct keys. Generating the
    identifiers here rather than reusing the two constants above is what makes
    this a check on the *generator*'s output reaching the hash, rather than on
    two literals that happen to differ.
    """
    keys = {
        fixture_key(InvocationRequest(prompt="hello", model="m", trace_id=generate_trace_id()))
        for _ in range(6)
    }
    assert len(keys) == 1, f"six runs of one request produced {len(keys)} distinct keys: {keys}"


def test_the_hashed_set_is_the_declared_fields_less_the_stated_exception() -> None:
    """TR-020's closure restated with its hole, against the model that ships.

    The equality is written as a *derivation* on both sides — declared fields
    minus serialization-excluded minus `UNHASHED_REQUEST_FIELDS` — so a field
    added to `InvocationRequest` is in the hashed set by default and a second
    exception has to be spelled into the named constant to escape it. A test
    comparing against a hand-kept list would pass while the two drifted.
    """
    request = InvocationRequest(prompt="hello", model="m", trace_id=A_TRACE_ID)
    serializable = {
        name for name, field in InvocationRequest.model_fields.items() if not field.exclude
    }

    assert set(hashed_request_payload(request)) == serializable - UNHASHED_REQUEST_FIELDS
    assert "trace_id" in serializable, (
        "trace_id was excluded on the model, which drops it from every "
        "serialization of a request rather than from the hash alone"
    )


def test_the_stated_exception_is_a_correlation_identifier_and_nothing_else() -> None:
    """The hole is one field wide, and a widening should fail here first.

    `UNHASHED_REQUEST_FIELDS` is the only place TR-020's closure can be opened,
    so this is the check that a later change to it is deliberate. Excluding a
    field that can change what the provider is asked would give two genuinely
    different calls one key — the direction that serves a fixture recorded for
    something else, which is the failure that looks like everything working.
    """
    assert set(UNHASHED_REQUEST_FIELDS) == {"trace_id"}
    assert set(InvocationRequest.model_fields) >= UNHASHED_REQUEST_FIELDS, (
        "an excluded name is not a declared field, so it excludes nothing and "
        "the closure it appears to open is not the one it opens"
    )


def test_the_repair_key_is_trace_id_invariant_too() -> None:
    """A repair is a second fixture, keyed on the original request.

    It derives from `fixture_key`, so it inherits the fix rather than needing
    its own — but it is asserted separately because that derivation is an
    implementation detail a later change could drop, and a repair that replayed
    as a miss would look exactly like a fixture recorded before the repair path
    existed.
    """
    one = InvocationRequest(prompt="hello", model="m", trace_id=A_TRACE_ID)
    two = InvocationRequest(prompt="hello", model="m", trace_id=ANOTHER_TRACE_ID)

    assert repair_fixture_key(one, "fix field x") == repair_fixture_key(two, "fix field x")
    assert repair_fixture_key(one, "fix field x") != repair_fixture_key(one, "fix field y"), (
        "the repair instruction stopped reaching the key"
    )


# --- TR-038: the two derived versions ---------------------------------------


class Bounded(BaseModel):
    value: int = Field(ge=1, le=10)


class Wider(BaseModel):
    value: int = Field(ge=1, le=100)


def test_the_schema_version_is_a_digest_not_a_caller_string() -> None:
    """TR-038 forbids accepting either version as a caller-declared string.

    A caller-declared version is a promise nobody checks — it stays the same
    while the schema changes, and every stale fixture keeps matching.
    """
    assert schema_version(Bounded).startswith(f"{HASH_ALGORITHM}:")


def test_a_changed_validator_changes_the_schema_version() -> None:
    """The staleness path the digest exists to catch.

    `Bounded` and `Wider` have the same field names and types and different
    *constraints*. A digest over field names alone would call them identical,
    and a fixture recorded under one would replay under the other — which is
    exactly the case a validator edit introduces.
    """
    assert schema_version(Bounded) != schema_version(Wider)


def test_the_schema_version_is_stable_for_one_schema() -> None:
    assert schema_version(Bounded) == schema_version(Bounded)


def test_the_prompt_template_version_digests_the_resolved_text() -> None:
    """Resolved, not the template source: two templates that resolve to the same
    text produce the same call and must share a key."""
    assert prompt_template_version("Summarise: X") == prompt_template_version("Summarise: X")
    assert prompt_template_version("Summarise: X") != prompt_template_version("Summarise: Y")


def test_the_derived_versions_reach_the_fixture_key() -> None:
    """TR-019 names both among the hashed inputs, so a schema or template change
    must change the key even when every request field is identical. Without
    this, a validator edit would keep replaying fixtures recorded before it."""
    request = Request(provider="p", model="m", prompt="hello")
    with_bounded = fixture_key(request, schema=Bounded, template="T")
    with_wider = fixture_key(request, schema=Wider, template="T")
    other_template = fixture_key(request, schema=Bounded, template="U")

    assert with_bounded != with_wider, "the schema digest does not reach the key"
    assert with_bounded != other_template, "the template digest does not reach the key"
    assert with_bounded != fixture_key(request), "the digests are silently optional"
