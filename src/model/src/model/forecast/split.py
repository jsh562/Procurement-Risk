"""The held-out split: stratified on censoring status, keyed on nothing else.

AD-011. Neither the seed nor the fraction is a call argument — both are
committed constants in `config.py`, because a per-run value lets a re-fit
reshuffle the split until a vendor lands favourably, which is FR-028's
prohibition reached by another route. What remains is a pure function of
`(input_data_hash, SPLIT_SEED, HELD_OUT_FRACTION)` and the rows: there is no
freedom left to exercise, which is what makes the split evidence rather than a
by-product (SC-015 forbids committing it early, so ordering cannot do the job).

Stratified because an unstratified draw hits the aggregate fraction while
putting the censored lines wherever the shuffle left them, and FR-006's realized
held-out uncensored event count is then an accident.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from model.corpus.manifest import DIGEST_PATTERN
from model.forecast.censoring import censoring_indicator
from model.forecast.config import HELD_OUT_FRACTION, SPLIT_SEED
from model.forecast.read import LineRow
from model.forecast.serialize import split_assignment_hash

__all__ = [
    "HELD_OUT",
    "TRAIN",
    "SplitAssignment",
    "SplitError",
    "SplitResult",
    "assign_split",
]


class SplitError(ValueError):
    """Raised when a cohort cannot be split deterministically.

    A `ValueError`: every case is something the caller handed over — no lines,
    two lines claiming one natural key, or a digest that is not one.
    """


#: The two sides `ck_forecast_split_assignment__side` admits.
TRAIN = "train"
HELD_OUT = "held_out"


@dataclass(frozen=True, slots=True)
class SplitAssignment:
    """One line's side, in the shape `forecast_split_assignment` stores it.

    Carries the five serialized fields plus `po_line_id`, which is half the
    table's primary key, and `canonical_ordinal`, which is the position the
    digest's sequence is ordered by. Frozen, because the assignment is the
    evidence AD-011 rests on and a mutable one could be edited after the fit
    it was supposed to have preceded.
    """

    po_line_id: uuid.UUID
    project_id: str
    po_number: str
    line_number: int
    split_side: str
    is_censored: bool
    canonical_ordinal: int


@dataclass(frozen=True, slots=True)
class SplitResult:
    """The whole assignment and the digest taken over it, returned together.

    One object rather than two returns, for the reason `read_roster` gives:
    a caller able to obtain the assignment without its digest would eventually
    record one without the other, and the digest is what FR-023 refuses on.
    """

    assignments: tuple[SplitAssignment, ...]
    split_assignment_hash: str


def _checked(lines: Sequence[LineRow], input_data_hash: str) -> None:
    """Every precondition this module owns, before a single side is decided.

    The as-of date is absent because `censoring_indicator` validates it, and it
    is the only thing here that reads one; checking it twice would be two
    opinions about what a run anchor is.
    """
    if not lines:
        raise SplitError(
            "the split was asked to assign zero lines; `read.py` refuses an empty "
            "`purchase_order_line` for the same reason, and an empty assignment would "
            "produce a run whose held-out set asserts nothing"
        )
    if not isinstance(input_data_hash, str) or not DIGEST_PATTERN.fullmatch(input_data_hash):
        raise SplitError(
            f"{input_data_hash!r} is not a `sha256:`-prefixed lowercase hex digest. The "
            f"split is keyed on the input row hash (AD-011), so a malformed key would "
            f"produce an assignment that no re-fit could reproduce"
        )
    keys = [line.natural_key for line in lines]
    if len(set(keys)) != len(keys):
        duplicated = sorted({key for key in keys if keys.count(key) > 1})
        raise SplitError(
            f"the natural key repeats at {duplicated}; `uq_purchase_order_line__natural` "
            f"makes that unrepresentable, and the canonical order has no tie-break because "
            f"it needs none"
        )


def _draw_key(line: LineRow, input_data_hash: str) -> str:
    """This line's position in the shuffle, as a digest of the three determinants.

    A digest rather than a seeded PRNG stream: a stream's output depends on the
    order rows are consumed in, so an unsorted frame would assign different
    sides to the same cohort. Keyed material is delimited by a character no
    identifier may contain, so two different field splits cannot collide into
    one string.

    The as-of date is deliberately absent. AD-011 names three determinants and
    it is not among them — it reaches the split only by deciding the stratum.
    """
    material = "|".join(
        (
            input_data_hash,
            str(SPLIT_SEED),
            repr(HELD_OUT_FRACTION),
            line.project_id,
            line.po_number,
            str(line.line_number),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _held_out_quota(stratum_size: int) -> int:
    """How many of this stratum's lines are held out — the nearest whole line.

    Rounded to nearest rather than rounded up, which is what keeps a stratum of
    one on the training side: `HELD_OUT_FRACTION` of one line is a quarter of a
    line, the nearest whole number of lines is none, and rounding up would hold
    out the only observation and train on nothing. `0.25` is exact in binary
    and the size is an integer, so the product carries no representation error
    to round through.
    """
    return math.floor(HELD_OUT_FRACTION * stratum_size + 0.5)


def _sides_within(stratum: list[LineRow], input_data_hash: str) -> dict[uuid.UUID, str]:
    """One stratum's lines split at the declared fraction, held-out first.

    Ordered by the per-line draw key with the natural key as the tie-break, so
    the ordering is total even in the (unreachable, since SHA-256) case of two
    equal digests, and the result never depends on the order the rows arrived
    in.
    """
    ordered = sorted(stratum, key=lambda line: (_draw_key(line, input_data_hash), line.natural_key))
    quota = _held_out_quota(len(ordered))
    return {
        line.po_line_id: (HELD_OUT if position < quota else TRAIN)
        for position, line in enumerate(ordered)
    }


def assign_split(lines: Iterable[LineRow], as_of_date: date, input_data_hash: str) -> SplitResult:
    """Assign every line to `train` or `held_out`, stratified on censoring status.

    Returns the assignment in ascending `(project_id, po_number, line_number)`
    with `canonical_ordinal` contiguous from 1 — a gap would make the hashed
    input a sequence with a hole in it, well formed by every constraint and not
    the thing that was hashed — together with the digest over it (FR-005,
    FR-006, FR-007).
    """
    rows = tuple(lines)
    _checked(rows, input_data_hash)

    censored = {line.po_line_id: censoring_indicator(line, as_of_date) for line in rows}
    sides: dict[uuid.UUID, str] = {}
    for stratum in (True, False):
        members = [line for line in rows if censored[line.po_line_id] is stratum]
        sides.update(_sides_within(members, input_data_hash))

    assignments = tuple(
        SplitAssignment(
            po_line_id=line.po_line_id,
            project_id=line.project_id,
            po_number=line.po_number,
            line_number=line.line_number,
            split_side=sides[line.po_line_id],
            is_censored=censored[line.po_line_id],
            canonical_ordinal=ordinal,
        )
        for ordinal, line in enumerate(sorted(rows, key=lambda line: line.natural_key), start=1)
    )
    return SplitResult(
        assignments=assignments, split_assignment_hash=split_assignment_hash(assignments)
    )
