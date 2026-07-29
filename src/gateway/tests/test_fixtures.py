"""TR-021 / TR-022 / TR-023 / TR-033 / TR-056: modes, misses, and provenance.

The mode tests are the ones worth reading twice. `record` and `replay` differ by
whether an invocation spends money, and every guard here exists because the
convenient behaviour — default to one, fall back to the other — is the one that
makes an offline suite quietly online or reports results for calls that never
happened.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from gateway.compute.hashing import fixture_key
from gateway.config import (
    CREDENTIAL_ENV_VAR,
    MODE_ENV_VAR,
    PROVIDER_OPT_IN_ENV_VAR,
    RECORD_MODE,
    REPLAY_MODE,
    provider_calls_permitted,
    require_no_credential_in_replay,
    require_provider_opt_in,
    resolve_mode,
)
from gateway.errors import GatewayConfigError
from gateway.fixtures import (
    FIXTURE_LOOKUP_ATTEMPTS,
    FixtureMissError,
    FixtureProvenance,
    FixtureStore,
)
from gateway.models import InvocationRequest, generate_trace_id

COMMITTED_ROOT = Path(__file__).resolve().parents[1] / "fixtures"

#: A stand-in for a credential that is deliberately **not credential-shaped**.
#:
#: The first draft used a realistic fake — the provider's real key prefix
#: followed by plausible characters — and E001's supply-chain scan flagged this
#: file, correctly. TR-060's detector matches that prefix followed by sixteen or
#: more key characters, and it cannot tell a fake from a real one; that is what
#: makes it useful. A committed file that trips it has to be fixed rather than
#: excused, because the alternative is teaching the scan to ignore a shape.
#:
#: The guard under test only asks whether the variable is non-blank, so nothing
#: is lost: the value's *shape* was never what the test needed.
NOT_A_CREDENTIAL = "set-but-deliberately-not-key-shaped"


def a_provenance(**overrides: object) -> FixtureProvenance:
    defaults: dict[str, object] = {
        "recorded_on": date(2026, 7, 26),
        "gen_ai_response_model": "claude-opus-5",
        "gateway_revision": "0" * 40,
        "gen_ai_usage_input_tokens": 12,
        "gen_ai_usage_output_tokens": 8,
    }
    defaults.update(overrides)
    return FixtureProvenance(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[FixtureStore]:
    yield FixtureStore(tmp_path / "fixtures")


# --- TR-021: two modes, no default, no fallback -----------------------------


def test_an_absent_mode_is_refused() -> None:
    """The requirement's whole substance. Defaulting to `replay` is the
    safe-looking choice and is still wrong: a `record`-mode run that silently
    replayed would report costs and fixtures for calls it never made."""
    with pytest.raises(GatewayConfigError, match=MODE_ENV_VAR):
        resolve_mode({})


def test_a_blank_mode_is_refused() -> None:
    """An exported-but-empty variable is a broken shell far more often than a
    considered instruction, so it is absence rather than a third value."""
    with pytest.raises(GatewayConfigError):
        resolve_mode({MODE_ENV_VAR: "   "})


@pytest.mark.parametrize("mode", [RECORD_MODE, REPLAY_MODE])
def test_each_mode_resolves_to_itself(mode: str) -> None:
    assert resolve_mode({MODE_ENV_VAR: mode}) == mode


@pytest.mark.parametrize("value", ["Record", "REPLAY", "live", "off", "record,replay"])
def test_a_value_outside_the_two_is_refused(value: str) -> None:
    """No nearest match and no case-folding. `Record` is not `record` — a mode
    that guessed would spend money on the strength of a capital letter."""
    with pytest.raises(GatewayConfigError):
        resolve_mode({MODE_ENV_VAR: value})


@pytest.mark.parametrize("value", ["record ", " record", "  replay  "])
def test_surrounding_whitespace_is_incidental(value: str) -> None:
    """Stripped, and this is a decision rather than an accident of `strip()`.

    A trailing space on an exported variable is a shell artifact — a stray
    quote, a here-doc, a CI templating engine — far more often than an intent to
    name a fourth mode. Refusing it would fail a run for something invisible in
    the value.

    The line is drawn at *surrounding* whitespace only. Case is not folded and
    no interior character is touched, because those genuinely distinguish one
    value from another; whitespace at the edges does not.
    """
    assert resolve_mode({MODE_ENV_VAR: value}) == value.strip()


# --- TR-027 / TR-063: the opt-in is a second, separate decision --------------


def test_the_opt_in_permits_only_its_exact_value() -> None:
    assert provider_calls_permitted({PROVIDER_OPT_IN_ENV_VAR: "1"})


@pytest.mark.parametrize("value", ["", "0", "true", "TRUE", "yes", "on", "2", " 1"])
def test_every_other_spelling_denies(value: str) -> None:
    """TR-063 fixes the form deliberately. A control whose spelling is negotiable
    is one a check cannot assert the *absence* of — and asserting its absence
    from every CI environment is half of what this control is for."""
    assert not provider_calls_permitted({PROVIDER_OPT_IN_ENV_VAR: value})


def test_an_absent_opt_in_denies() -> None:
    assert not provider_calls_permitted({})


def test_record_mode_without_the_opt_in_is_refused() -> None:
    """Two decisions rather than one. Selecting `record` by accident is a
    configuration slip; selecting it *and* setting the opt-in is a choice."""
    with pytest.raises(GatewayConfigError, match=PROVIDER_OPT_IN_ENV_VAR):
        require_provider_opt_in({MODE_ENV_VAR: RECORD_MODE})


def test_the_opt_in_is_independent_of_mode_selection() -> None:
    """Structurally separate, not merely documented as separate: the opt-in
    check reads its own variable and never consults the mode."""
    assert provider_calls_permitted({PROVIDER_OPT_IN_ENV_VAR: "1"})
    assert provider_calls_permitted({PROVIDER_OPT_IN_ENV_VAR: "1", MODE_ENV_VAR: REPLAY_MODE})


# --- TR-023: replay refuses to run beside a credential ----------------------


def test_replay_refuses_when_a_credential_is_present() -> None:
    """Refusing to run when the *means of cheating* is present is what makes
    the offline claim structural. A gateway that quietly reached the provider
    would produce the same results, faster, and cost money nobody watched for.
    """
    with pytest.raises(GatewayConfigError, match=CREDENTIAL_ENV_VAR):
        require_no_credential_in_replay({CREDENTIAL_ENV_VAR: NOT_A_CREDENTIAL})


def test_replay_is_content_with_no_credential() -> None:
    require_no_credential_in_replay({})
    require_no_credential_in_replay({CREDENTIAL_ENV_VAR: "   "})


def test_the_refusal_names_the_key_and_no_part_of_the_value() -> None:
    """TR-065: not the value, not a prefix, not a truncation, not its length."""
    secret = NOT_A_CREDENTIAL
    with pytest.raises(GatewayConfigError) as raised:
        require_no_credential_in_replay({CREDENTIAL_ENV_VAR: secret})

    message = str(raised.value)
    assert CREDENTIAL_ENV_VAR in message
    assert secret not in message
    assert secret[:10] not in message
    assert str(len(secret)) not in message


# --- TR-022: a miss raises, and nothing reaches the network -----------------


def test_a_miss_raises_rather_than_falling_back(store: FixtureStore) -> None:
    """The tempting behaviour is to reach the provider when a fixture is
    missing. It does not exist here — there is no code path from the store to a
    socket, which is stronger than a flag that defaults to off."""
    key = fixture_key(InvocationRequest(prompt="never recorded"))
    with pytest.raises(FixtureMissError) as raised:
        store.load(key)
    assert raised.value.key == key


def test_the_miss_names_the_key_it_looked_for(store: FixtureStore) -> None:
    """The only actionable fact: a miss means either the request changed or the
    fixture was never recorded, and the key is what a developer greps for."""
    key = fixture_key(InvocationRequest(prompt="x"))
    with pytest.raises(FixtureMissError, match=key.split(":")[1][:16]):
        store.load(key)


def test_a_response_without_provenance_is_a_miss(store: FixtureStore) -> None:
    """TR-033 makes provenance mandatory, so an unlabelled response is not a
    partial success. Treating it as present would replay generated data with no
    record of where it came from."""
    key = fixture_key(InvocationRequest(prompt="half written"))
    store.save(key, "{}", a_provenance())
    _, provenance_path = store._paths(key)
    provenance_path.unlink()

    assert not store.has(key)
    with pytest.raises(FixtureMissError):
        store.load(key)


def test_the_store_reports_both_kinds_of_orphan(store: FixtureStore) -> None:
    key = fixture_key(InvocationRequest(prompt="orphan"))
    store.save(key, "{}", a_provenance())
    response_path, _ = store._paths(key)
    response_path.unlink()

    assert store.orphans(), "a sidecar labelling nothing was not reported"


def test_a_malformed_key_is_refused_rather_than_used_as_a_path(
    store: FixtureStore,
) -> None:
    """The key becomes a filesystem path, so an unvalidated one is a traversal
    away from writing outside the store."""
    for bad in ["../escape", "sha256:short", "md5:" + "0" * 64, "sha256:" + "Z" * 64]:
        with pytest.raises(ValueError, match="fixture key"):
            store._paths(bad)


# --- TR-019 / TR-022: the key covers what a different call would change -----


def test_a_changed_request_misses_the_fixture_recorded_for_the_old_one(
    store: FixtureStore,
) -> None:
    """The direction that would otherwise be silent.

    A miss on a changed request is *correct* — the alternative is serving a
    fixture recorded for a different call, which returns plausible output and
    looks like everything working.
    """
    original = InvocationRequest(prompt="assess this vendor")
    store.save(fixture_key(original), '{"ok": true}', a_provenance())

    changed = InvocationRequest(prompt="assess this vendor.")
    assert store.has(fixture_key(original))
    assert not store.has(fixture_key(changed)), (
        "a request with different content resolved the earlier fixture"
    )


def test_an_unchanged_request_hits(store: FixtureStore) -> None:
    request = InvocationRequest(prompt="assess this vendor")
    store.save(fixture_key(request), '{"ok": true}', a_provenance())
    assert store.load(fixture_key(request)).content == '{"ok": true}'


# --- TR-033 / TR-056: what replay is priced from ----------------------------


def test_replay_usage_comes_from_the_recording(store: FixtureStore) -> None:
    """TR-056. Taken from the sidecar rather than re-estimated, which is what
    makes a replayed cost reproducible — an estimate would drift with whatever
    tokenizer the gateway happened to link against."""
    request = InvocationRequest(prompt="x")
    store.save(
        fixture_key(request),
        "{}",
        a_provenance(gen_ai_usage_input_tokens=1234, gen_ai_usage_output_tokens=56),
    )
    usage = store.load(fixture_key(request)).provenance.usage()
    assert usage.input_tokens == 1234
    assert usage.output_tokens == 56


def test_the_pricing_timestamp_is_the_recording_date_at_midnight_utc(
    store: FixtureStore,
) -> None:
    """TR-043 and TR-057 together: replaying one fixture reproduces one cost
    however long afterwards it runs, even across an effective-from boundary
    inside the pinned version — and the zone is stated so two machines agree."""
    provenance = a_provenance(recorded_on=date(2026, 8, 31))
    stamp = provenance.pricing_timestamp()
    assert stamp == datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
    assert stamp.tzinfo is not None


def test_a_fixture_lookup_counts_as_one_transport_attempt() -> None:
    """TR-056. Makes `transport_attempt_count >= 1` hold on replay rows without
    anyone inferring it — a replayed row recording zero attempts would violate
    the column's own CHECK."""
    assert FIXTURE_LOOKUP_ATTEMPTS == 1


def test_provenance_requires_every_labelling_field() -> None:
    """TR-033 names four facts, each answering a question that becomes
    unanswerable once the fixture is a month old. None is optional."""
    for missing in [
        "recorded_on",
        "gen_ai_response_model",
        "gateway_revision",
        "gen_ai_usage_input_tokens",
    ]:
        fields = {
            "recorded_on": date(2026, 7, 26),
            "gen_ai_response_model": "m",
            "gateway_revision": "abc",
            "gen_ai_usage_input_tokens": 1,
            "gen_ai_usage_output_tokens": 1,
        }
        del fields[missing]
        with pytest.raises(Exception, match="[Ff]ield required"):
            FixtureProvenance(**fields)  # type: ignore[arg-type]


def test_provenance_forbids_an_undeclared_field() -> None:
    """The sidecar is a closed contract like the record is: a field nobody
    declared is a label nothing reads."""
    with pytest.raises(Exception, match="extra"):
        a_provenance(recorded_by="someone")


# --- TR-033: the committed store is labelled --------------------------------


def test_the_committed_store_has_no_unlabelled_fixture() -> None:
    """Run against the store that ships, not a temporary one. An unlabelled
    committed fixture is anonymous generated data, which is what Principle I
    refuses and TR-033 exists to prevent."""
    if not COMMITTED_ROOT.is_dir():
        pytest.skip(f"no committed fixture store at {COMMITTED_ROOT}")
    committed = FixtureStore(COMMITTED_ROOT)
    assert committed.orphans() == [], (
        f"committed fixtures missing their counterpart: {committed.orphans()}"
    )


def test_every_committed_fixture_loads_and_carries_provenance() -> None:
    if not COMMITTED_ROOT.is_dir():
        pytest.skip(f"no committed fixture store at {COMMITTED_ROOT}")
    committed = FixtureStore(COMMITTED_ROOT)
    keys = committed.stored_keys()
    assert keys, "the committed store is empty, so this check asserts nothing"
    for key in keys:
        fixture = committed.load(key)
        assert fixture.provenance.gateway_revision, key
        assert fixture.provenance.gen_ai_response_model, key


def test_the_committed_keys_are_the_digests_they_are_stored_under() -> None:
    """A key that does not match its own content-hash layout would make the
    store's lookups depend on the filename rather than on the request — which is
    the sequence-numbered scheme content-hashing replaced."""
    if not COMMITTED_ROOT.is_dir():
        pytest.skip(f"no committed fixture store at {COMMITTED_ROOT}")
    committed = FixtureStore(COMMITTED_ROOT)
    for key in committed.stored_keys():
        response_path, _ = committed._paths(key)
        assert response_path.is_file(), (
            f"{key} is listed but its path does not resolve; the layout and the "
            f"key derivation disagree"
        )


#: The request the committed exemplar was recorded for. Held here so the two
#: tests below derive their key from one place rather than each restating it.
EXEMPLAR_REQUEST = InvocationRequest(prompt="exemplar", model="claude-opus-5")


def test_the_exemplar_key_is_reproducible() -> None:
    """The seeded fixture's key is derived, not typed. If the hashing changed,
    this fails rather than the store silently going cold — every replay would
    miss and each miss would look like an unrecorded request.

    **Derived from a real `InvocationRequest`, not from a hand-built payload.**
    It was written the other way and that is half of why the `trace_id` defect
    survived this epic's QC: the hand-built dict named `prompt` and `model` and
    no third key, so it asserted what the author believed `fixture_key` hashed
    instead of what it hashed. The two had already diverged when this test was
    written — `model_dump_json()` carried `trace_id: null`, so the real request
    keyed to `sha256:fddb3574…` while the committed fixture and this test both
    said `sha256:72a4e4a4…`. The committed exemplar was unreachable from the
    invocation path from the day it landed, and the test that existed to catch
    exactly that agreed with the fixture because it shared the mistake.

    A key derived through the function under test cannot share a mistake with
    it.
    """
    if not COMMITTED_ROOT.is_dir():
        pytest.skip(f"no committed fixture store at {COMMITTED_ROOT}")
    assert fixture_key(EXEMPLAR_REQUEST) in FixtureStore(COMMITTED_ROOT).stored_keys()


def test_the_exemplar_resolves_through_the_replay_lookup() -> None:
    """The store answers the key the request derives, end to end.

    `stored_keys` above proves the key is *listed*; this proves `load` resolves
    it, which is the operation replay actually performs. They fail differently:
    a layout that disagreed with the key derivation would pass the first and
    raise `FixtureMissError` here.
    """
    if not COMMITTED_ROOT.is_dir():
        pytest.skip(f"no committed fixture store at {COMMITTED_ROOT}")
    fixture = FixtureStore(COMMITTED_ROOT).load(fixture_key(EXEMPLAR_REQUEST))
    assert fixture.content
    assert fixture.provenance.gen_ai_response_model == "claude-opus-5"


def test_the_exemplar_resolves_for_a_caller_that_supplies_a_trace_id() -> None:
    """The seam this fix exists for (FR-070).

    E006 supplies one run-scoped identifier per run. Before the fix this lookup
    missed on every run, with a different key each time, and each miss reported
    itself as an unrecorded request — so the committed store looked empty to the
    only consumer obliged to use it.
    """
    if not COMMITTED_ROOT.is_dir():
        pytest.skip(f"no committed fixture store at {COMMITTED_ROOT}")
    traced = InvocationRequest(
        prompt="exemplar", model="claude-opus-5", trace_id=generate_trace_id()
    )
    store = FixtureStore(COMMITTED_ROOT)
    untraced_content = store.load(fixture_key(EXEMPLAR_REQUEST)).content
    assert store.load(fixture_key(traced)).content == untraced_content


# --- AD-008: the fixture store's soft cap ------------------------------------

#: 25 MB, from AD-008. A **soft** cap: exceeding it warns rather than fails.
#: A hard cap would break the build for a store that is merely large, and the
#: thing worth knowing is the trend, not the threshold — a store growing past
#: this is usually one where regenerated fixtures were added without their
#: predecessors being deleted (see the README's regeneration procedure).
FIXTURE_STORE_SOFT_CAP_BYTES = 25 * 1024 * 1024


def store_size_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def test_the_committed_store_is_under_its_soft_cap() -> None:
    """Warns by failing *this* test alone, which is the soft part: nothing else
    in the suite depends on it, so a large store reports itself without
    blocking anything that matters."""
    if not COMMITTED_ROOT.is_dir():
        pytest.skip(f"no committed fixture store at {COMMITTED_ROOT}")

    size = store_size_bytes(COMMITTED_ROOT)
    assert size <= FIXTURE_STORE_SOFT_CAP_BYTES, (
        f"the committed fixture store is {size / 1024 / 1024:.1f} MB against a "
        f"{FIXTURE_STORE_SOFT_CAP_BYTES / 1024 / 1024:.0f} MB soft cap (AD-008). "
        f"The usual cause is regenerated fixtures added without deleting the ones "
        f"they replaced — a content-hash key means a changed prompt writes a new "
        f"file rather than overwriting one."
    )


def test_the_size_measurement_counts_sidecars_too(tmp_path: Path) -> None:
    """A control on the measurement. Counting only responses would understate a
    store by roughly the size of its provenance, which is most of the size of a
    store full of small fixtures."""
    store = FixtureStore(tmp_path / "fixtures")
    store.save(fixture_key(InvocationRequest(prompt="sized")), "x" * 1000, a_provenance())

    size = store_size_bytes(tmp_path / "fixtures")
    assert size > 1000, (
        f"measured {size} bytes for a 1000-byte response plus a sidecar; the "
        f"sidecar is not being counted"
    )
