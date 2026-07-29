"""The computed per-field confidence. Deterministic, and not the model's opinion.

FR-030 / FR-031 / FR-057, Principle V. Every extracted value carries a score,
and the score is arithmetic over three observable parse signals — never a number
the model asserted about its own output. TR-081 was amended for exactly this
(FR-047): a self-reported confidence is not reproducible and cannot be
unit-tested, and research finds it collapses toward all-positive at practical
thresholds, so a floor over it would reject nothing.

**The three signals, and the fourth that was withdrawn.** Whether the printed
field label matched the canonical form or a known alternate; whether the value
was read from one chunk or assembled across a page break; whether the invocation
validated on the first attempt or only after a repair. A fourth — whether the
value was printed or absent — was withdrawn during clarification and is **not**
computed: an absent value is a failure record rather than a stored value with a
confidence, so the signal could never fire on a row this module ranges over.

**Deductions from 1.0, applied left to right in the declared order.** The order
is `alternate label`, then `page split`, then `repaired`, and it is part of the
record rather than an implementation detail: `double precision` subtraction is
not associative, so `1.0 - a - p` and `1.0 - (a + p)` need not be bit-identical,
and SC-026 requires a recomputation to reproduce a stored score **exactly**.
`data-model.md` §`ingestion_run` fixes the order; `DEDUCTION_ORDER` names it so
the report can publish it (FR-046) rather than only obeying it.

**No weight and no floor is a constant in this module, and that is the design.**
They are columns on `ingestion_run` (FR-032, FR-046, FR-057), so a stored score
is checkable against *the policy that produced it* rather than against whatever
happens to be checked out. A weight left in code recomputes to a different number
with no symptom at all — the recomputation succeeds and simply agrees with a
policy the row was never scored under. The declared values are `ingest/runs.py`'s
and reach this module as an argument.

**What is not enforced here.** FR-057's two named exclusions — the floor rejects
any repaired invocation, and any value both alternate-labelled and page-split —
are `ck_ingestion_run__floor_excludes_repair` and
`ck_ingestion_run__floor_excludes_alt_split` on the run row. Restating them here
would be a second statement of a rule the database already refuses to store a
run without, and the two could then disagree. This module computes; the row
declares; the report publishes.

This module imports nothing from `model.ingest`, `model.llm`, or `gateway`. It
is pure: same input, same output, no clock, no locale, and no database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

__all__ = [
    "DEDUCTION_ORDER",
    "LABEL_MATCHES",
    "LABEL_MATCH_ALTERNATE",
    "LABEL_MATCH_CANONICAL",
    "SIGNAL_DOMAIN",
    "ConfidenceError",
    "DeductionWeights",
    "ParseSignals",
    "compute_confidence",
]


class ConfidenceError(ValueError):
    """A signal set or a weight set is outside what a stored row can hold.

    One type for every refusal, as the rest of `model.compute` uses: each of
    them means the same thing to a caller — this score is not computed, because
    computing it would produce a number no column could accept or no row could
    explain.
    """


#: `ck_extracted_value_parse_signal__label_match`'s closed set, restated so a
#: third value is refused here rather than by a constraint after the row is
#: built. A named vocabulary rather than a boolean `was_alternate`, matching the
#: column: it says which of two stated things the label was, not whether an
#: unstated default did not hold.
LABEL_MATCH_CANONICAL: Final[str] = "canonical"
LABEL_MATCH_ALTERNATE: Final[str] = "alternate"
LABEL_MATCHES: Final[tuple[str, ...]] = (LABEL_MATCH_CANONICAL, LABEL_MATCH_ALTERNATE)

#: The application order, named so FR-046 can publish it. These are the field
#: names of `DeductionWeights` and the suffixes of the three `ingestion_run`
#: columns, in the one order `data-model.md` fixes.
DEDUCTION_ORDER: Final[tuple[str, ...]] = ("alternate_label", "page_split", "repaired")


def _finite_unit_interval(value: float, field: str) -> float:
    """A weight the run row's range check would accept, or a refusal.

    NaN is rejected explicitly rather than left to the comparison. `nan <= 1.0`
    is false, so a range check written as `if not 0.0 <= value <= 1.0: raise`
    happens to catch it — but one written the other way round would admit it
    silently, and a NaN weight makes every score NaN with no error anywhere.
    """
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise ConfidenceError(
            f"{field} is {value!r}. `ck_ingestion_run__deduction_{field}_range` admits a "
            f"finite value in [0.0, 1.0]; a non-finite weight makes every score it "
            f"touches non-finite with no error anywhere."
        )
    if not 0.0 <= number <= 1.0:
        raise ConfidenceError(
            f"{field} is {number}, outside the [0.0, 1.0] that "
            f"`ck_ingestion_run__deduction_{field}_range` admits"
        )
    return number


@dataclass(frozen=True)
class ParseSignals:
    """The three observable signals one value's confidence is computed from.

    The field names and value domains are `extracted_value_parse_signal`'s
    (FR-063), so a signal row read back from the database constructs one of
    these without translation — which is what makes SC-026's recomputation a
    read rather than an interpretation.

    **`source_chunk_count`, not a `page_split` boolean.** The page-split signal
    already exists in E003's `extracted_value.source_chunk_count`, so an
    independent boolean would be a second answer that can disagree with the
    value's own provenance — and the disagreement would be invisible, because
    the recomputation would read the copy while the citation read the original.
    The signal row carries the count and a composite foreign key holds it equal
    to the value's, so `page_split` is derived here and cannot differ from what
    `extracted_value_contributing_chunk` actually holds.
    """

    label_match: str
    source_chunk_count: int
    validated_after_repair: bool

    def __post_init__(self) -> None:
        if self.label_match not in LABEL_MATCHES:
            raise ConfidenceError(
                f"label_match {self.label_match!r} is outside {list(LABEL_MATCHES)}, which "
                f"`ck_extracted_value_parse_signal__label_match` fixes. A third value is a "
                f"migration, not a new label."
            )
        if not isinstance(self.source_chunk_count, int) or isinstance(
            self.source_chunk_count, bool
        ):
            raise ConfidenceError(
                f"source_chunk_count is {self.source_chunk_count!r}; the column is a "
                f"`smallint` and the page-split signal is a comparison against it, not a "
                f"boolean supplied beside it"
            )
        if self.source_chunk_count < 1:
            raise ConfidenceError(
                f"source_chunk_count is {self.source_chunk_count}, which "
                f"`ck_extracted_value_parse_signal__source_count_positive` refuses. A "
                f"stored value is assembled from at least one chunk; zero would make the "
                f"page-split signal read False for a value with no provenance at all."
            )
        if not isinstance(self.validated_after_repair, bool):
            raise ConfidenceError(
                f"validated_after_repair is {self.validated_after_repair!r}; the column is "
                f"a boolean, and FR-057 deducts once for a repaired invocation regardless "
                f"of how many attempts were spent"
            )

    @property
    def alternate_label(self) -> bool:
        """FR-057's first signal: the printed label was a known alternate."""
        return self.label_match == LABEL_MATCH_ALTERNATE

    @property
    def page_split(self) -> bool:
        """FR-057's second signal, derived from the value's own chunk count."""
        return self.source_chunk_count > 1

    @property
    def description(self) -> str:
        """The combination as the ingestion report labels it (FR-033, FR-046)."""
        return (
            f"{self.label_match} label / "
            f"{'page-split' if self.page_split else 'single-chunk'} / "
            f"{'repaired' if self.validated_after_repair else 'first attempt'}"
        )


def _domain() -> tuple[ParseSignals, ...]:
    """The eight combinations FR-057's three binary signals admit.

    Enumerated in a fixed order — canonical before alternate, single-chunk
    before page-split, first-attempt before repaired — so FR-033's published
    distribution has a stable row order across runs rather than one that depends
    on how a dictionary happened to iterate.

    `2` is the representative page-split count. The deduction is taken once
    however many chunks contributed, so any count above one denotes the same
    combination; two is the smallest, and using the smallest keeps the
    enumeration from implying a count the corpus has to produce.
    """
    return tuple(
        ParseSignals(label_match=label, source_chunk_count=count, validated_after_repair=repaired)
        for label in LABEL_MATCHES
        for count in (1, 2)
        for repaired in (False, True)
    )


#: FR-033: the distribution is published over **all eight** scores the signals
#: admit, a score nothing took appearing as a zero rather than as an absent row.
#: That is only checkable against an enumeration of the domain, so the
#: enumeration is a published object rather than a loop inside the report.
SIGNAL_DOMAIN: Final[tuple[ParseSignals, ...]] = _domain()


@dataclass(frozen=True)
class DeductionWeights:
    """FR-057's three weights, as they are read off the run row.

    Field names are the suffixes of `ingestion_run.deduction_alternate_label`,
    `deduction_page_split` and `deduction_repaired`, so a row constructs one of
    these without a mapping step nobody would think to test.

    **The values are not defaulted.** There is no declared triple here: the
    declaration is `ingest/runs.py`'s and reaches the database before the first
    document, and a default here would be a second declaration that silently
    wins whenever a caller forgot to read the row.
    """

    alternate_label: float
    page_split: float
    repaired: float

    def __post_init__(self) -> None:
        for field in DEDUCTION_ORDER:
            object.__setattr__(self, field, _finite_unit_interval(getattr(self, field), field))
        # Spelled as the worst achievable score rather than as a sum, so this
        # guard and `compute_confidence` do the same arithmetic in the same
        # order: a rule written as `a + p + r <= 1.0` could admit a triple the
        # computation then drives below zero, in the last bit.
        worst = ((1.0 - self.alternate_label) - self.page_split) - self.repaired
        if worst < 0.0:
            raise ConfidenceError(
                f"the three deductions drive the all-signals score to {worst}, below zero. "
                f"`ck_extracted_value__confidence_range` admits [0.0, 1.0], so under this "
                f"policy one of the eight combinations has no representable score. The "
                f"run row cannot state this rule — it is a cross-column condition revision "
                f"`0400` does not carry, and an applied revision is not edited — so it is "
                f"refused where the policy is declared rather than at the end of a run."
            )


def compute_confidence(signals: ParseSignals, weights: DeductionWeights) -> float:
    """The confidence one extracted value carries (FR-030, FR-031, FR-057).

    Args:
        signals: the three parse signals, exactly as
            `extracted_value_parse_signal` records them. Derived by
            deterministic code from the parse — never asserted by the model.
        weights: the run's three deduction weights, read from its own
            `ingestion_run` row. Passed rather than looked up, because this
            module holds no connection and because a score is only checkable
            against the policy the caller can name.

    Returns:
        `1.0` less each deduction whose signal fired, applied **left to right**
        in `DEDUCTION_ORDER` and skipping the terms whose signals are absent.
        The order is the requirement, not a convention: SC-026 requires bit
        equality on recomputation and `double precision` subtraction is not
        associative.

    Raises:
        ConfidenceError: never from here. Both arguments validate at
            construction, so an inadmissible signal set or weight set is
            unrepresentable rather than caught at the call site.

    **The floor is not applied here.** A score at or above the run's declared
    floor is persisted with its confidence intact and one below it is recorded
    as a failure with outcome `confidence_below_threshold` (FR-032) — that is a
    decision about *storing*, and it belongs to the orchestrator that holds both
    the score and the row. Returning `None` below the floor, or clamping, would
    make this function's return value depend on a policy it was not given.
    """
    score = 1.0
    if signals.alternate_label:
        score = score - weights.alternate_label
    if signals.page_split:
        score = score - weights.page_split
    if signals.validated_after_repair:
        score = score - weights.repaired
    return score
