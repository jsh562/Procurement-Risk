"""Cost arithmetic and the within-version price lookup — pure, exact, offline.

TR-014, TR-016, TR-039, TR-049, TR-057. Behind the computation boundary
(TR-032): `gateway.provider` may not import this module, directly or through an
intermediate, so the orchestration module above both composes them. That is what
makes Principle V — *the model extracts, code computes* — a property of the
import graph rather than a habit.

**Nothing here reaches a database, a clock, or a network.** `resolve_price_entry`
takes the candidate entries as an argument rather than querying for them. The
lookup rule is the part worth testing exhaustively, and a rule that needs a
live PostgreSQL to exercise gets tested for the two cases someone set up. The
query that produces the candidates lives in the record writer, where the
connection already is.

**Decimal throughout, never float** (CD-3, TR-049). SC-006 asserts that
recomputation reproduces a stored cost *exactly*, and binary floating point
cannot carry that claim — the failure would appear only for the values that
happen to be unrepresentable in binary, which is the worst possible
distribution of a bug. `Decimal` in, `Decimal` out, and the boundary with
psycopg is `NUMERIC` on both sides.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Final

from gateway.errors import GatewayError

__all__ = [
    "COST_SCALE",
    "MAX_COST_USD",
    "RATE_SCALE",
    "CostOutOfRangeError",
    "PriceEntry",
    "PriceRates",
    "TokenCounts",
    "compute_cost",
    "pricing_date",
    "resolve_price_entry",
]

#: `NUMERIC(18,10)` on `llm_invocation.cost_usd`, fixed by TR-049 rather than
#: chosen here.
COST_SCALE: Final[int] = 10

#: `NUMERIC(12,6)` on every rate column, also fixed by TR-049.
RATE_SCALE: Final[int] = 6

#: Rates are published per million tokens, so the sum of the four terms is
#: divided by this exactly once.
TOKENS_PER_MILLION: Final[Decimal] = Decimal(1_000_000)

#: The smallest representable cost, and the unit every quantization uses.
COST_QUANTUM: Final[Decimal] = Decimal(1).scaleb(-COST_SCALE)

#: `NUMERIC(18,10)` is eighteen significant digits with ten after the point, so
#: eight before it. Derived from the two scales rather than written as a
#: literal, because a literal here and a column type in the migration are two
#: statements of one fact with nothing comparing them.
MAX_COST_USD: Final[Decimal] = Decimal(10) ** (18 - COST_SCALE) - COST_QUANTUM

#: Precision for the intermediate sum. Comfortably above anything the four
#: terms can produce — ten million tokens at a scale-6 rate is at most 13
#: integer digits and 12 fractional — so the "full precision" CD-2 requires is
#: literal rather than approximate, and the single quantization at the end is
#: the only rounding that happens.
_WORKING_PRECISION: Final[int] = 60


class CostOutOfRangeError(GatewayError):
    """The computed cost cannot be stored at the schema's scale.

    Its own type, and it raises rather than returning a value, because TR-049
    is explicit that this is **not** a rounding case: the row is written with
    cost absent and reason `cost_out_of_range`, never with a truncated figure.
    A function that returned the clamped value would let a caller store it by
    doing nothing wrong.

    Reachable only from a defective rate or token count — `INTEGER` tokens times
    a `NUMERIC(12,6)` rate can exceed the range arithmetically — so this is a
    defined outcome for a case that should not arise, rather than an expected
    one.
    """

    def __init__(self, computed: Decimal) -> None:
        super().__init__(
            f"computed cost {computed} lies outside the representable range "
            f"[0, {MAX_COST_USD}] of NUMERIC(18,{COST_SCALE}); the invocation is "
            f"recorded with cost absent and reason 'cost_out_of_range' (TR-049)"
        )
        self.computed = computed


@dataclass(frozen=True, slots=True)
class TokenCounts:
    """The four billing classes, summed across every attempt of one invocation.

    Summed *first*, then priced and quantized once (CD-2) — SC-017's "total
    spend" is that figure, never a sum of separately quantized per-attempt
    costs. An attempt that returned no response body contributes zero rather
    than leaving a term undefined (TR-056).
    """

    input_tokens: int
    cache_write_input_tokens: int
    cache_read_input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class PriceRates:
    """The four rates from one `price_table_entry`, in USD per million tokens.

    Kept separate from `PriceEntry` so the arithmetic can be exercised without
    inventing a model identifier and an effective-from date it does not read.
    """

    input_usd_per_mtok: Decimal
    cache_write_usd_per_mtok: Decimal
    cache_read_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal


@dataclass(frozen=True, slots=True)
class PriceEntry:
    """One row of `price_table_entry`, as the lookup sees it."""

    model_id: str
    effective_from: date
    rates: PriceRates


def compute_cost(counts: TokenCounts, rates: PriceRates) -> Decimal:
    """The cost of one invocation, in United States dollars.

    CD-2, and the ordering is contractual rather than incidental: **sum all four
    terms at full precision, then quantize exactly once** under round-half-even.
    Per-term quantization produces a different figure and is a defect, not a
    variant — each term is a token count times a scale-6 rate over a million, so
    it carries up to twelve decimal places against a stored scale of ten.
    Rounding four times discards two digits four times; rounding once discards
    them once, and the two can differ by a full unit in the last place.

    Round-half-even rather than half-up because half-up biases every recorded
    cost upward by a systematic fraction — invisible in one row and
    accumulating across all of them.

    Args:
        counts: Token counts by billing class, already summed across attempts.
        rates: The four rates from the resolved price entry.

    Returns:
        The cost, at exactly `COST_SCALE` decimal places.

    Raises:
        CostOutOfRangeError: The figure does not fit `NUMERIC(18,10)`.
    """
    with localcontext() as context:
        # Raised locally rather than globally: the default context's 28 digits
        # would silently round the intermediate sum, which is the one place
        # CD-2 requires full precision. Scoped so no other arithmetic in the
        # process inherits it.
        context.prec = _WORKING_PRECISION
        context.rounding = ROUND_HALF_EVEN

        total = (
            Decimal(counts.input_tokens) * rates.input_usd_per_mtok
            + Decimal(counts.cache_write_input_tokens) * rates.cache_write_usd_per_mtok
            + Decimal(counts.cache_read_input_tokens) * rates.cache_read_usd_per_mtok
            + Decimal(counts.output_tokens) * rates.output_usd_per_mtok
        ) / TOKENS_PER_MILLION

        cost = total.quantize(COST_QUANTUM, rounding=ROUND_HALF_EVEN)

    # Checked after quantization, not before. A figure a hair over the maximum
    # that rounds down to exactly the maximum is representable, and rejecting it
    # would make the largest storable cost unreachable by arithmetic.
    if cost < 0 or cost > MAX_COST_USD:
        raise CostOutOfRangeError(cost)
    return cost


def pricing_date(pricing_timestamp: datetime) -> date:
    """The UTC calendar date a pricing timestamp falls on (TR-057, CD-1, HINT-006).

    The zone is stated rather than inherited, and that is the entire point. A
    bare `timestamp.date()` uses whatever offset the value carries, and SQL's
    `pricing_timestamp::date` resolves against the session `TimeZone` — a
    setting neither this module nor the migration controls. Either would let one
    row price differently on two machines, which is the failure CD-1 names.

    A naive timestamp is rejected rather than assumed to be UTC: assuming is how
    a local-time value silently shifts an invocation across an `effective_from`
    boundary, and the shift is at most a day, so it is wrong exactly at the
    boundaries that matter.
    """
    if pricing_timestamp.tzinfo is None or pricing_timestamp.utcoffset() is None:
        raise ValueError(
            "pricing_timestamp must be timezone-aware; a naive value would be "
            "read as local time and could resolve a different price entry on "
            "another machine (TR-057)"
        )
    return pricing_timestamp.astimezone(UTC).date()


def resolve_price_entry(
    entries: Iterable[PriceEntry],
    model_id: str,
    pricing_timestamp: datetime,
) -> PriceEntry | None:
    """The entry covering `model_id` at `pricing_timestamp`, or None.

    TR-039 and TR-057. Three rules, each of which has a wrong version that looks
    reasonable:

    **Within the pinned version only.** `entries` is already scoped to the pin
    by the caller's query; this function never widens it. There is no fallback
    to another version, because a cost priced against a version the row does not
    cite is unauditable — the stored `price_table_version_id` would not explain
    the stored figure.

    **Exact, case-sensitive model equality.** No normalization, no casefolding,
    no prefix match, no nearest model. TR-016 forbids the nearest-match half and
    TR-057 states the positive half; both are needed for the lookup to be
    decidable. A near miss is `None` here and `cost_absent_reason =
    'no_covering_price_entry'` on the row — absent with a reason, which TR-016
    requires, rather than a plausible figure from a different model.

    **Latest effective-from at or before the date, compared in UTC.** Determinism
    rests on `(version, model_id, effective_from)` being unique (VR-010), not on
    the ordering: with a duplicate representable, `max` would break the tie
    arbitrarily while looking correct. The database refuses to represent it, so
    the tie cannot occur — and this function does not pretend to resolve one.

    Returns:
        The covering entry, or `None` when the version holds no entry for the
        model at or before that date. `None` is a lookup outcome, not an error:
        TR-016 makes it `cost_absent_reason='no_covering_price_entry'`.
    """
    as_of = pricing_date(pricing_timestamp)
    covering = [
        entry for entry in entries if entry.model_id == model_id and entry.effective_from <= as_of
    ]
    if not covering:
        return None
    return max(covering, key=lambda entry: entry.effective_from)
