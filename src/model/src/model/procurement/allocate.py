"""Who supplies what: the declared line allocation across vendors and projects.

`data-model.md` § Allocation solves this and `tasks.md` § Design constants
records the solution. This module is that solution in code — it does **not**
re-derive it, and it consumes no randomness.

**Why the counts are declared rather than drawn.** FR-004's claimed 0.22–0.67
shrinkage span is a *consequence* of the per-vendor line counts through
ρⱼ = τ²/(τ² + σ²/nⱼ). Draw the counts and the span becomes whatever the draw
happened to produce, and a requirement stating a range would be asserting
something no longer under the generator's control. 5 and 35 are the endpoints
because they are the counts that reproduce the claimed span; the threefold
range in ρ is what makes partial pooling visibly *differential* rather than a
uniform nudge toward the grand mean.

**Why the fill is greedy rather than random.** Both margins must be met exactly
(DV-002). A random assignment respecting one margin leaves the other to chance,
and a cross-tab that is off by one in two cells still sums to a legal line
count — the failure mode a total-only check cannot see. Vendors are taken in
ascending id, and each line is dealt to the project with the largest remaining
quota, ties broken by ascending `project_id`. Both tie-breaks are on identifier
order rather than on iteration order, because a dict's iteration order is a
property of insertion history and would make the allocation depend on how this
module was written rather than on what it declares.

**No identifier literals.** FR-001 requires project and vendor identities to
come from the roster, and T032 scans this package's source for `PRJ-` and
`VND-` literals. The vectors here are keyed by *position*, and the identifiers
are resolved from `read_roster().identifiers()` at call time — so a roster that
renamed a vendor produces a renamed allocation rather than a silent mismatch
against a hard-coded key.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from model.roster.reader import read_roster

__all__ = [
    "DECLARED_TOTAL",
    "PO_SIZE_CYCLE",
    "PROJECT_LINE_COUNTS",
    "TAU",
    "VENDOR_LINE_COUNTS",
    "WITHIN_VENDOR_SIGMA",
    "AllocatedLine",
    "AllocationError",
    "allocate_lines",
    "shrinkage",
]

#: Within-vendor log spread, back-solved from the product document's published
#: 61-day median and 94-day P80: ln(94/61) / z₀.₈₀. Quoted from
#: `data-model.md` § Duration model, never re-solved here.
WITHIN_VENDOR_SIGMA = 0.51

#: Between-vendor log spread, 0.24 × σ_w. The ratio τ/σ_w is the quantity
#: FR-008 bands and FR-036 decomposes; this module needs it only to state the
#: shrinkage identity the line counts were chosen against.
TAU = 0.1224

#: Per-vendor line counts **by descending rank**, not by identifier. Rank 0 is
#: the largest vendor. Σ = 199.
_VENDOR_COUNTS_BY_RANK = (35, 28, 24, 21, 18, 16, 14, 12, 10, 9, 7, 5)

#: Per-project line counts by ascending identifier order: four at 40 and one at
#: 39, summing to the same 199.
_PROJECT_COUNTS_BY_RANK = (40, 40, 40, 40, 39)

DECLARED_TOTAL = sum(_VENDOR_COUNTS_BY_RANK)

#: Share of lines that pass through at least one rework loop, and how those
#: lines split across one, two and three loops. **Declared, not drawn** — DV-009
#: requires the realized allocation to *equal* the declared one, not merely be
#: recorded near it, so a draw would put a criterion at the mercy of the seed.
#:
#: `L = round(0.30 × N)` = 60 at N = 199, split `(42, 13, 5)`. The three-loop
#: stratum is protected at five: largest-remainder apportionment would round it
#: away at smaller totals, and a rework depth that never occurs is a state
#: machine path the dataset claims to exercise and does not.
REWORK_LINE_SHARE = 0.30
_REWORK_SPLIT_BY_LOOPS = (42, 13, 5)
REWORK_MAX_LOOPS = len(_REWORK_SPLIT_BY_LOOPS)
_THREE_LOOP_FLOOR = 5

#: The cyclic purchase-order size pattern. Sizes above one exist so
#: `line_number >= 2` is exercised against the delivered contiguity and
#: natural-key constraints — a table of one-line orders would test neither.
#:
#: **The cycle advances continuously across `(project, vendor)` groups rather
#: than resetting inside each one.** `data-model.md` reads either way, and the
#: difference is not cosmetic: 199 lines spread over 60 groups is ~3.3 lines per
#: group, so a per-group reset never advances past index 2 and the pattern's
#: `3` — and its second `2` — would never occur. Measured, that reset produces
#: 142 one-line, 27 two-line and **1** three-line order. Continuous, every
#: declared size occurs and three-line orders reach ~4%.
#:
#: What is *not* claimed: that the realized size distribution matches the
#: cycle's own shape. The cycle's composition — 60% ones, 20% twos, 10% threes —
#: is not realized, because `min(size, remaining)` clips whenever a group has
#: fewer lines left than the cycle calls for, which at 3.3 lines per group is
#: most of the time. `data-model.md` declares a cycle and requires only that
#: every PO's lines share a project and a vendor and that `line_number >= 2` is
#: exercised; it states no target distribution, and inventing one here and then
#: shaping the algorithm to hit it would be asserting a number no artifact
#: derives. The shortfall is recorded as a disclosed gap instead.
#:
#: Continuity costs no determinism: groups are consumed in sorted order, so the
#: index a group starts at is a function of the declared vectors alone.
PO_SIZE_CYCLE = (1, 1, 2, 1, 3, 1, 1, 2, 1, 1)


class AllocationError(ValueError):
    """Raised when the declared vectors cannot be reconciled with a requested total."""


def _identifiers() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Project and vendor identifiers, in ascending order, from the roster.

    Read from `Roster.projects` and `Roster.vendors` rather than from
    `identifiers()`, which flattens both into one set — correct for the
    membership check DV-001 makes, and useless here, where the two vectors are
    allocated against different margins and must not be conflated.
    """
    roster = read_roster()
    return (
        tuple(sorted(entry.id for entry in roster.projects)),
        tuple(sorted(entry.id for entry in roster.vendors)),
    )


def _scaled(counts_by_rank: tuple[int, ...], total: int) -> tuple[int, ...]:
    """Rescale a declared vector to `total`, preserving shape and exactness.

    Largest-remainder apportionment, so the rescaled vector sums to `total`
    exactly rather than to `total ± rounding`. The shape is preserved — the
    largest stratum stays largest — because the boundary cases the property
    tests sweep (190, 200, 210) are meant to exercise the same allocation under
    a different total, not a different allocation.

    Every stratum is floored at one: a rescale that emptied a vendor would drop
    a whole stratum from the cross-tab, and DV-001 requires all twelve present.
    """
    declared = sum(counts_by_rank)
    if total == declared:
        return counts_by_rank
    if total < len(counts_by_rank):
        raise AllocationError(
            f"cannot allocate {total} lines across {len(counts_by_rank)} strata "
            f"while keeping every stratum non-empty"
        )
    exact = [count * total / declared for count in counts_by_rank]
    floors = [max(1, int(value)) for value in exact]
    remainder = total - sum(floors)
    order = sorted(
        range(len(counts_by_rank)),
        key=lambda i: (-(exact[i] - int(exact[i])), i),
    )
    step = 1 if remainder > 0 else -1
    index = 0
    while remainder != 0:
        position = order[index % len(order)]
        if step == -1 and floors[position] <= 1:
            index += 1
            if index > len(order) * total:  # pragma: no cover — unreachable, guards a spin
                raise AllocationError(f"cannot reduce to {total} with every stratum non-empty")
            continue
        floors[position] += step
        remainder -= step
        index += 1
    return tuple(floors)


def _vector(ids: tuple[str, ...], counts_by_rank: tuple[int, ...], total: int) -> Mapping[str, int]:
    if len(ids) != len(counts_by_rank):
        raise AllocationError(
            f"the roster supplies {len(ids)} identifiers but the declared vector has "
            f"{len(counts_by_rank)} strata; the allocation is declared against a fixed "
            f"roster shape and a changed roster is a regeneration, not a rescale"
        )
    return MappingProxyType(dict(zip(ids, _scaled(counts_by_rank, total), strict=True)))


def _declared(counts_by_rank: tuple[int, ...], which: int) -> Mapping[str, int]:
    return _vector(_identifiers()[which], counts_by_rank, sum(counts_by_rank))


class _LazyVector(Mapping[str, int]):
    """The declared vector, resolved against the roster on first use.

    A module-level `dict` would read the roster at import time, which turns any
    import of this package into a file read and makes an unrelated test fail
    with a roster error. `Mapping` rather than a function because the property
    tests and DV-002 both want to compare against it as data.
    """

    __slots__ = ("_counts", "_which")

    def __init__(self, counts_by_rank: tuple[int, ...], which: int) -> None:
        self._counts = counts_by_rank
        self._which = which

    def _resolved(self) -> Mapping[str, int]:
        return _declared(self._counts, self._which)

    def __getitem__(self, key: str) -> int:
        return self._resolved()[key]

    def __iter__(self):
        return iter(self._resolved())

    def __len__(self) -> int:
        return len(self._counts)


#: The declared per-vendor and per-project vectors, keyed by roster identifier.
VENDOR_LINE_COUNTS: Mapping[str, int] = _LazyVector(_VENDOR_COUNTS_BY_RANK, 1)
PROJECT_LINE_COUNTS: Mapping[str, int] = _LazyVector(_PROJECT_COUNTS_BY_RANK, 0)


def shrinkage(line_count: int) -> float:
    """ρ = τ²/(τ² + σ_w²/n) — the weight a vendor's own mean carries.

    Stated here rather than in `durations.py` because it is the identity the
    line counts were *chosen against*: 5 and 35 are the endpoints precisely
    because they reproduce FR-004's 0.22–0.67 span. Keeping the identity beside
    the counts is what lets a property test check the two against each other
    instead of checking each against a transcribed number.
    """
    if line_count < 1:
        raise AllocationError(
            f"shrinkage is undefined at a line count of {line_count}; ρ requires n ≥ 1"
        )
    return TAU**2 / (TAU**2 + WITHIN_VENDOR_SIGMA**2 / line_count)


def rework_loop_allocation(line_count: int = DECLARED_TOTAL) -> tuple[int, ...]:
    """How many rework loops each line takes, as a declared per-stratum count.

    Returns a tuple of length `line_count` whose entries are 0–3, ordered so
    that looped lines come first. **Callers must not read positional meaning
    into it** — the event walk pairs it with lines by natural-key order, which
    is deterministic, and the calibration only needs the multiset.

    `L = round(0.30 × N)` looped lines, split across one, two and three loops
    by largest-remainder apportionment with the three-loop stratum protected at
    five. Realized must *equal* declared (DV-009).

    One definition, consumed twice: the event walk builds the chains from it and
    the `T_PRE` calibration draws its population from it. Two statements of one
    declared allocation is how the calibrated constant silently stops matching
    the data it was calibrated for.
    """
    if line_count < 1:
        raise AllocationError(f"cannot allocate rework across {line_count} lines")
    looped = round(REWORK_LINE_SHARE * line_count)
    if looped == 0:
        return (0,) * line_count

    declared = sum(_REWORK_SPLIT_BY_LOOPS)
    exact = [count * looped / declared for count in _REWORK_SPLIT_BY_LOOPS]
    counts = [int(value) for value in exact]
    counts[-1] = max(counts[-1], min(_THREE_LOOP_FLOOR, looped))

    remainder = looped - sum(counts)
    order = sorted(range(len(counts)), key=lambda i: (-(exact[i] - int(exact[i])), i))
    index = 0
    while remainder != 0:
        position = order[index % len(order)]
        step = 1 if remainder > 0 else -1
        if step == -1 and position == len(counts) - 1 and counts[position] <= _THREE_LOOP_FLOOR:
            index += 1
            continue
        if step == -1 and counts[position] == 0:
            index += 1
            continue
        counts[position] += step
        remainder -= step
        index += 1

    allocation: list[int] = []
    for loops, count in enumerate(counts, start=1):
        allocation.extend([loops] * count)
    allocation.extend([0] * (line_count - len(allocation)))
    return tuple(allocation)


@dataclass(frozen=True, slots=True)
class AllocatedLine:
    """One line's structural identity, before any value is drawn for it.

    Carries only what the allocation decides. Everything else — dates,
    quantities, the manufacturer, the event chain — is drawn downstream against
    the seed addressed by this line's natural key, which is why the natural key
    must be settled before any draw happens.
    """

    project_id: str
    vendor_id: str
    po_number: str
    line_number: int

    @property
    def natural_key(self) -> tuple[str, str, int]:
        return (self.project_id, self.po_number, self.line_number)


def allocate_lines(total: int | None = None) -> tuple[AllocatedLine, ...]:
    """Deal `total` lines across the declared vendor and project margins.

    Returns lines in ascending natural-key order. `total` defaults to the
    declared 199 and exists for the property tests to sweep the band edges and
    the *N* = 200 crossover; production generation never passes it.
    """
    project_ids, vendor_ids = _identifiers()
    resolved = DECLARED_TOTAL if total is None else total
    vendor_quota = dict(_vector(vendor_ids, _VENDOR_COUNTS_BY_RANK, resolved))
    project_remaining = dict(_vector(project_ids, _PROJECT_COUNTS_BY_RANK, resolved))

    # Greedy fill: vendors in ascending id, each line to the project with the
    # largest remaining quota, ties by ascending project_id. Both margins are
    # therefore met exactly rather than approximately.
    pairs: list[tuple[str, str]] = []
    for vendor_id in vendor_ids:
        for _ in range(vendor_quota[vendor_id]):
            project_id = max(
                project_ids,
                key=lambda pid: (project_remaining[pid], [-ord(c) for c in pid]),
            )
            if project_remaining[project_id] <= 0:
                raise AllocationError(
                    f"vendor {vendor_id} has quota left but every project is full; "
                    f"the two declared margins disagree at total {resolved}"
                )
            project_remaining[project_id] -= 1
            pairs.append((project_id, vendor_id))

    # Group into purchase orders within each (project, vendor) pair, following
    # the cyclic size pattern. PO numbers are sequential within a project, so a
    # PO number is unique inside its project without encoding the vendor.
    grouped: dict[tuple[str, str], int] = {}
    for pair in pairs:
        grouped[pair] = grouped.get(pair, 0) + 1

    po_counter = dict.fromkeys(project_ids, 0)
    lines: list[AllocatedLine] = []
    cycle_index = 0
    for (project_id, vendor_id), count in sorted(grouped.items()):
        remaining = count
        while remaining > 0:
            size = min(PO_SIZE_CYCLE[cycle_index % len(PO_SIZE_CYCLE)], remaining)
            cycle_index += 1
            po_counter[project_id] += 1
            po_number = f"PO-{po_counter[project_id]:05d}"
            for line_number in range(1, size + 1):
                lines.append(AllocatedLine(project_id, vendor_id, po_number, line_number))
            remaining -= size

    lines.sort(key=lambda line: line.natural_key)
    return tuple(lines)
