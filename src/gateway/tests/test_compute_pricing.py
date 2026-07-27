"""TR-014 / TR-049 (CD-2, CD-3): cost is exact, and exactly reproducible.

Written before `compute/pricing.py` exists, per the project's test-first rule
for deterministic computation. Property-based rather than example-based because
the claim SC-006 makes is universally quantified — *recomputation reproduces the
stored cost exactly* — and a handful of examples cannot distinguish that from
"reproduces it for the cases someone thought of".

**Why the quantization ordering gets its own property.** `CD-2` fixes it as
contractual: sum all four billing-class terms at full precision, then quantize
once. Per-term quantization is a defect, not a variant. That is easy to write
either way and impossible to tell apart from a single example — each term is a
token count times a scale-6 rate divided by a million, so each carries up to
twelve decimal places while the stored scale is ten. Rounding four terms
individually discards two digits four times; rounding the sum discards them
once. The test below constructs a case where the two differ by a full unit in
the last place.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from gateway.compute.pricing import (
    COST_SCALE,
    MAX_COST_USD,
    RATE_SCALE,
    CostOutOfRangeError,
    PriceEntry,
    PriceRates,
    TokenCounts,
    compute_cost,
    pricing_date,
    resolve_price_entry,
)

#: Token counts a real invocation could carry, with headroom. The upper bound
#: is far above any single request's context window on purpose — the arithmetic
#: must not stop being exact at the edge of what looks plausible today.
tokens = st.integers(min_value=0, max_value=10_000_000)

#: Rates at the scale the schema stores (`NUMERIC(12,6)`). Generated as integers
#: and scaled, rather than from `st.decimals`, so every value is exactly
#: representable at scale 6 and the strategy cannot produce an input the
#: database would reject.
rates = st.integers(min_value=0, max_value=1_000_000_000).map(
    lambda micro: Decimal(micro).scaleb(-RATE_SCALE)
)


@st.composite
def token_counts(draw: st.DrawFn) -> TokenCounts:
    return TokenCounts(
        input_tokens=draw(tokens),
        cache_write_input_tokens=draw(tokens),
        cache_read_input_tokens=draw(tokens),
        output_tokens=draw(tokens),
    )


@st.composite
def price_rates(draw: st.DrawFn) -> PriceRates:
    return PriceRates(
        input_usd_per_mtok=draw(rates),
        cache_write_usd_per_mtok=draw(rates),
        cache_read_usd_per_mtok=draw(rates),
        output_usd_per_mtok=draw(rates),
    )


def _in_range(counts: TokenCounts, price: PriceRates) -> bool:
    try:
        compute_cost(counts, price)
    except CostOutOfRangeError:
        return False
    return True


# --- CD-2: the arithmetic ----------------------------------------------------


@given(token_counts(), price_rates())
@settings(max_examples=300)
def test_cost_is_deterministic(counts: TokenCounts, price: PriceRates) -> None:
    """The whole of SC-006 rests on this. A cost that varies between two calls
    on one machine cannot reproduce a stored figure on another."""
    assume(_in_range(counts, price))
    assert compute_cost(counts, price) == compute_cost(counts, price)


@given(token_counts(), price_rates())
@settings(max_examples=300)
def test_cost_is_a_decimal_at_the_stored_scale(counts: TokenCounts, price: PriceRates) -> None:
    """CD-3. `NUMERIC` in, `Decimal` out, with no binary float anywhere on the
    path — a float intermediate would make "exactly" a matter of tolerance, and
    the failure would appear only for the values that happen to be
    unrepresentable in binary."""
    assume(_in_range(counts, price))
    cost = compute_cost(counts, price)
    assert isinstance(cost, Decimal)
    assert -cost.as_tuple().exponent == COST_SCALE, (
        f"cost {cost} is at scale {-cost.as_tuple().exponent}, not {COST_SCALE}"
    )


@given(token_counts(), price_rates())
@settings(max_examples=300)
def test_cost_is_never_negative(counts: TokenCounts, price: PriceRates) -> None:
    """The schema's `CHECK` says the same thing; asserting it here means a
    negative figure is caught before the write rather than as a constraint
    violation on an invocation the provider was already paid for."""
    assume(_in_range(counts, price))
    assert compute_cost(counts, price) >= 0


@given(price_rates())
def test_zero_tokens_cost_nothing(price: PriceRates) -> None:
    """Zero is a real value here, not a stand-in for unknown — an invocation
    that failed before any usage was reported genuinely cost nothing, and TR-016
    keeps that distinct from cost *absent*."""
    free = TokenCounts(0, 0, 0, 0)
    assert compute_cost(free, price) == Decimal(0).quantize(Decimal(1).scaleb(-COST_SCALE))


@given(token_counts(), price_rates(), st.integers(min_value=1, max_value=100_000))
@settings(max_examples=200)
def test_more_output_tokens_never_cost_less(
    counts: TokenCounts, price: PriceRates, extra: int
) -> None:
    """Monotonicity. Not required by any requirement in so many words, and
    that is the point: it is a property the arithmetic must have for the figure
    to mean anything, and it would fail loudly if a term were ever subtracted
    or a rate applied to the wrong count."""
    more = TokenCounts(
        counts.input_tokens,
        counts.cache_write_input_tokens,
        counts.cache_read_input_tokens,
        counts.output_tokens + extra,
    )
    assume(_in_range(counts, price) and _in_range(more, price))
    assert compute_cost(more, price) >= compute_cost(counts, price)


@given(token_counts(), price_rates())
@settings(max_examples=200)
def test_the_four_terms_are_each_applied_to_their_own_rate(
    counts: TokenCounts, price: PriceRates
) -> None:
    """Guards the mistake that produces a plausible number: pairing a count
    with the wrong rate. Recomputed here from the definition rather than by
    calling a helper the implementation also uses — a shared helper would agree
    with itself no matter which pairing it chose."""
    assume(_in_range(counts, price))
    expected = (
        counts.input_tokens * price.input_usd_per_mtok
        + counts.cache_write_input_tokens * price.cache_write_usd_per_mtok
        + counts.cache_read_input_tokens * price.cache_read_usd_per_mtok
        + counts.output_tokens * price.output_usd_per_mtok
    ) / Decimal(1_000_000)
    quantum = Decimal(1).scaleb(-COST_SCALE)
    assert compute_cost(counts, price) == expected.quantize(quantum)


# --- CD-2: quantize once, at the end -----------------------------------------


def test_quantizing_once_differs_from_quantizing_each_term() -> None:
    """The case that makes the ordering contractual rather than stylistic.

    Each of the four terms is `2.5e-11` — a quarter of the stored scale's
    smallest unit. Rounded individually under round-half-even each becomes zero,
    so a per-term implementation reports a free invocation. Summed first they
    make `1e-10` exactly, which is one unit in the last place. The two
    implementations differ by the entire representable minimum, and no example
    with ordinary-looking numbers would show it.
    """
    counts = TokenCounts(1, 1, 1, 1)
    price = PriceRates(
        input_usd_per_mtok=Decimal("0.000025"),
        cache_write_usd_per_mtok=Decimal("0.000025"),
        cache_read_usd_per_mtok=Decimal("0.000025"),
        output_usd_per_mtok=Decimal("0.000025"),
    )
    quantum = Decimal(1).scaleb(-COST_SCALE)

    per_term = sum(
        ((Decimal(count) * rate) / Decimal(1_000_000)).quantize(quantum)
        for count, rate in (
            (counts.input_tokens, price.input_usd_per_mtok),
            (counts.cache_write_input_tokens, price.cache_write_usd_per_mtok),
            (counts.cache_read_input_tokens, price.cache_read_usd_per_mtok),
            (counts.output_tokens, price.output_usd_per_mtok),
        )
    )
    assert per_term == Decimal("0.0000000000"), (
        "the constructed case no longer distinguishes the two orderings; "
        "pick rates whose per-term value rounds to zero"
    )
    assert compute_cost(counts, price) == Decimal("0.0000000001"), (
        "cost was quantized per term rather than once at the end (CD-2)"
    )


@given(token_counts(), price_rates())
@settings(max_examples=300)
def test_cost_never_matches_the_per_term_ordering_when_the_two_disagree(
    counts: TokenCounts, price: PriceRates
) -> None:
    """The same rule, universally. Where the two orderings agree this asserts
    nothing; where they differ it pins which one the implementation follows."""
    assume(_in_range(counts, price))
    quantum = Decimal(1).scaleb(-COST_SCALE)
    per_term = sum(
        ((Decimal(count) * rate) / Decimal(1_000_000)).quantize(quantum)
        for count, rate in (
            (counts.input_tokens, price.input_usd_per_mtok),
            (counts.cache_write_input_tokens, price.cache_write_usd_per_mtok),
            (counts.cache_read_input_tokens, price.cache_read_usd_per_mtok),
            (counts.output_tokens, price.output_usd_per_mtok),
        )
    )
    exact = (
        Decimal(counts.input_tokens) * price.input_usd_per_mtok
        + Decimal(counts.cache_write_input_tokens) * price.cache_write_usd_per_mtok
        + Decimal(counts.cache_read_input_tokens) * price.cache_read_usd_per_mtok
        + Decimal(counts.output_tokens) * price.output_usd_per_mtok
    ) / Decimal(1_000_000)
    assume(per_term != exact.quantize(quantum))
    assert compute_cost(counts, price) == exact.quantize(quantum)


def test_rounding_is_half_even_not_half_up() -> None:
    """TR-049 names the mode. Half-up would bias every recorded cost upward by
    a systematic fraction, which is invisible per row and accumulates.

    Two cases, because half-even's whole character is that it breaks ties in
    both directions: an exact half rounds to the *even* neighbour, which is down
    from `...05` and up from `...15`.
    """
    quantum = Decimal(1).scaleb(-COST_SCALE)
    # 1 token at 0.000005 USD/Mtok = 5e-12 -- half of the last place at scale 10
    # would be 5e-11, so scale the count to land exactly on a tie.
    down = TokenCounts(10, 0, 0, 0)  # 10 * 5e-6 / 1e6 = 5e-11 -> ties to 0 (even)
    price = PriceRates(Decimal("0.000005"), Decimal(0), Decimal(0), Decimal(0))
    assert compute_cost(down, price) == Decimal(0).quantize(quantum)

    up = TokenCounts(30, 0, 0, 0)  # 1.5e-10 -> ties to 2e-10 (even)
    assert compute_cost(up, price) == Decimal("0.0000000002")


# --- TR-049: out of range is absent, never truncated -------------------------


def test_a_cost_beyond_the_stored_range_raises() -> None:
    """TR-049 is explicit that this is not a rounding case: a figure outside
    `NUMERIC(18,10)` is recorded as absent with reason `cost_out_of_range`,
    never truncated, rounded into range, or stored as a different number. The
    function raises so the caller must decide, rather than returning a value
    that looks like a cost."""
    # Both operands are at the top of what their columns can hold: `INTEGER`
    # tokens (max ~2.1e9) and a `NUMERIC(12,6)` rate (max 999999.999999). That
    # is what "reachable only from a defective rate or token count" means -- the
    # figures are representable and the product is not, which is exactly the
    # case TR-049 defines an outcome for.
    counts = TokenCounts(100_000_000, 100_000_000, 100_000_000, 100_000_000)
    price = PriceRates(
        Decimal("999999.999999"),
        Decimal("999999.999999"),
        Decimal("999999.999999"),
        Decimal("999999.999999"),
    )
    with pytest.raises(CostOutOfRangeError):
        compute_cost(counts, price)


def test_the_representable_maximum_itself_is_not_out_of_range() -> None:
    """An off-by-one at the boundary would make the largest legitimate cost
    unrecordable, and it would only ever be noticed by the invocation it
    happened to.

    Every input here is inside its column's domain, unlike the case above --
    so this asserts the boundary is reachable, not merely that a big number
    raises.
    """
    assert Decimal("99999999.9999999999") == MAX_COST_USD
    counts = TokenCounts(100_000_000, 0, 0, 0)
    price = PriceRates(Decimal("999999.999999"), Decimal(0), Decimal(0), Decimal(0))
    cost = compute_cost(counts, price)
    assert cost == Decimal("99999999.9999000000")
    assert cost <= MAX_COST_USD


# --- CD-3: the round trip ----------------------------------------------------


@given(token_counts(), price_rates())
@settings(max_examples=300)
def test_a_cost_survives_a_text_round_trip_exactly(counts: TokenCounts, price: PriceRates) -> None:
    """CD-3 as the driver will exercise it. `NUMERIC` arrives from psycopg as a
    `Decimal`, and the comparison must hold at the stored scale with no binary
    intermediate — so the value must survive serialization without acquiring or
    losing a digit."""
    assume(_in_range(counts, price))
    cost = compute_cost(counts, price)
    assert Decimal(str(cost)) == cost
    assert str(Decimal(str(cost))) == str(cost)


@given(token_counts(), price_rates())
@settings(max_examples=200)
def test_a_float_round_trip_is_not_relied_on(counts: TokenCounts, price: PriceRates) -> None:
    """The negative direction, and the reason CD-3 forbids a float path.

    This does not assert that float loses precision on every value — it does
    not. It asserts the implementation never returns something whose identity
    depends on that question, by checking the exact `Decimal` survives while
    saying nothing about the float.
    """
    assume(_in_range(counts, price))
    cost = compute_cost(counts, price)
    assert cost == Decimal(cost.as_tuple().sign and "-0" or "0") + cost


# --- TR-039 / TR-057 (CD-1): the within-version lookup -----------------------

_RATES = PriceRates(
    Decimal("5.000000"), Decimal("6.250000"), Decimal("0.500000"), Decimal("25.000000")
)
_LATER = PriceRates(
    Decimal("3.000000"), Decimal("3.750000"), Decimal("0.300000"), Decimal("15.000000")
)


def _at(moment: str) -> datetime:
    return datetime.fromisoformat(moment)


def test_the_latest_entry_at_or_before_the_timestamp_wins() -> None:
    """CD-1. `at or before`, so an entry effective on the day itself covers it."""
    entries = [
        PriceEntry("m", date(2026, 1, 1), _RATES),
        PriceEntry("m", date(2026, 9, 1), _LATER),
    ]
    chosen = resolve_price_entry(entries, "m", _at("2026-09-01T00:00:00+00:00"))
    assert chosen is not None and chosen.effective_from == date(2026, 9, 1)


def test_an_entry_effective_after_the_timestamp_is_not_used() -> None:
    """A scheduled future rate is seeded before it applies -- the price table
    carries one today. Selecting it early would price every invocation at a rate
    that has not taken effect."""
    entries = [
        PriceEntry("m", date(2026, 1, 1), _RATES),
        PriceEntry("m", date(2026, 9, 1), _LATER),
    ]
    chosen = resolve_price_entry(entries, "m", _at("2026-08-31T23:59:59+00:00"))
    assert chosen is not None and chosen.effective_from == date(2026, 1, 1)


def test_no_entry_at_or_before_the_timestamp_is_absent_not_nearest() -> None:
    """TR-016 forbids a nearest-match. An invocation predating every entry has
    no covering rate, and `None` here becomes `no_covering_price_entry` on the
    row -- absent with a reason, which is the whole of what TR-016 asks for."""
    entries = [PriceEntry("m", date(2026, 9, 1), _LATER)]
    assert resolve_price_entry(entries, "m", _at("2026-01-01T00:00:00+00:00")) is None


@pytest.mark.parametrize("queried", ["M", "m ", " m", "M ", "claude-OPUS-5"])
def test_model_matching_is_exact_and_case_sensitive(queried: str) -> None:
    """TR-057. No normalization, no casefolding, no prefix match, no fallback.

    A near miss must be absent rather than priced from a neighbouring model --
    the two can differ by a factor of ten, and a silently substituted rate
    produces a figure that looks entirely ordinary.
    """
    entries = [
        PriceEntry("m", date(2026, 1, 1), _RATES),
        PriceEntry("claude-opus-5", date(2026, 1, 1), _RATES),
    ]
    assert resolve_price_entry(entries, queried, _at("2026-06-01T00:00:00+00:00")) is None


def test_another_models_entries_are_never_consulted() -> None:
    """The same rule from the other side: a version holding rates for four
    models must not price a fifth from whichever happens to sort first."""
    entries = [
        PriceEntry("other", date(2026, 1, 1), _RATES),
        PriceEntry("another", date(2026, 1, 1), _LATER),
    ]
    assert resolve_price_entry(entries, "m", _at("2026-06-01T00:00:00+00:00")) is None


def test_the_caller_scopes_the_version_and_the_lookup_never_widens_it() -> None:
    """TR-039. `entries` is already scoped to the pin; there is no parameter by
    which this function could reach another version, which is the strongest
    form the rule can take -- a version it cannot name is a version it cannot
    consult."""
    import inspect

    parameters = set(inspect.signature(resolve_price_entry).parameters)
    assert parameters == {"entries", "model_id", "pricing_timestamp"}, (
        f"resolve_price_entry takes {sorted(parameters)}; a version or connection "
        f"parameter would let it widen the pin"
    )


# --- HINT-006 / TR-057: the zone is stated, not inherited --------------------


def test_a_naive_timestamp_is_refused() -> None:
    """Assuming UTC is how a local-time value silently shifts an invocation
    across an `effective_from` boundary -- and the shift is at most a day, so it
    is wrong exactly at the boundaries that decide the rate."""
    with pytest.raises(ValueError, match="timezone-aware"):
        pricing_date(datetime(2026, 9, 1, 0, 0, 0))


def test_the_same_instant_resolves_one_date_whatever_offset_it_carries() -> None:
    """CD-1. `2026-09-01T00:30+01:00` and `2026-08-31T23:30+00:00` are the same
    instant, and both are 31 August in UTC. An implementation calling
    `.date()` on the value as given would answer 1 September for the first --
    and price it at the newer rate."""
    east = _at("2026-09-01T00:30:00+01:00")
    utc = _at("2026-08-31T23:30:00+00:00")
    assert east == utc
    assert pricing_date(east) == pricing_date(utc) == date(2026, 8, 31)


def test_an_instant_straddling_the_boundary_resolves_the_earlier_entry() -> None:
    """The same case at the level the lookup sees it, since agreeing on the date
    is only useful if it changes which rate is chosen."""
    entries = [
        PriceEntry("m", date(2026, 1, 1), _RATES),
        PriceEntry("m", date(2026, 9, 1), _LATER),
    ]
    chosen = resolve_price_entry(entries, "m", _at("2026-09-01T00:30:00+01:00"))
    assert chosen is not None and chosen.effective_from == date(2026, 1, 1), (
        "an instant that is 31 August in UTC resolved the 1 September rate"
    )
