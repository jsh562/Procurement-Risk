"""T021 (RED) — the stratified split, at the mandatory property tier.

`plan.md` § Mandated properties gives `split.py` four relations. **Invariant**:
every line lands on exactly one side, with `canonical_ordinal` contiguous from 1
in ascending `(project_id, po_number, line_number)`. **Invariant**: each
stratum's realized proportion matches the declared fraction to within one line,
in both strata. **Metamorphic**: reordering the input rows changes no line's
side. **Invariant (AD-011)**: the assignment is a pure function of
`(input_data_hash, SPLIT_SEED, HELD_OUT_FRACTION)`. The module under test does
not exist yet — this file is the RED half of T021/T022.
"""

from __future__ import annotations

import inspect
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from model.forecast.config import HELD_OUT_FRACTION, SPLIT_SEED
from model.forecast.read import LifecycleEventRow, LineRow
from model.forecast.split import SplitAssignment, SplitResult, assign_split
from model.procurement.censor import AS_OF_DATE

NS = uuid.uuid5(uuid.NAMESPACE_URL, "e007/tests/forecast/split-properties")

#: The two sides `ck_forecast_split_assignment__side` admits.
TRAIN = "train"
HELD_OUT = "held_out"

#: The committed dataset's realized shape, named in the domain column: 24
#: censored lines against 175 delivered ones, 199 in total.
REALIZED_CENSORED = 24
REALIZED_DELIVERED = 175

ORDER_DATE = date(2025, 9, 1)

WALK = ("submitted", "under_review", "approved", "released_for_fabrication", "shipped", "delivered")


def input_hash(tag: str) -> str:
    """A digest of the surface form the split is keyed on.

    `ck_forecast_run__input_hash_format` admits `sha256:` and 64 hexadecimal
    characters, and two distinct tags must key two distinct assignments.
    """
    return "sha256:" + uuid.uuid5(NS, tag).hex * 2


def make_line(project_id: str, po_number: str, line_number: int, *, delivered: bool) -> LineRow:
    """One line, censored or delivered at `AS_OF_DATE` by construction.

    A delivered line carries the full six-event forward walk ending in a
    terminal event three months before the as-of date; a censored one stops at
    `shipped`. The stratum is therefore a property of the row rather than a flag
    the test passes in, which is what makes the stratification assertions about
    `split.py` rather than about their own fixtures.
    """
    po_line_id = uuid.uuid5(NS, f"pol|{project_id}|{po_number}|{line_number}")
    reached = WALK if delivered else WALK[:5]
    events = tuple(
        LifecycleEventRow(
            event_id=uuid.uuid5(NS, f"evt|{po_line_id}|{index + 1}"),
            po_line_id=po_line_id,
            sequence_no=index + 1,
            from_state=reached[index - 1] if index else None,
            to_state=state,
            is_terminal=state == "delivered",
            occurred_at=datetime(2025, 9, 1, tzinfo=UTC) + timedelta(days=14 * (index + 1)),
            note=None,
        )
        for index, state in enumerate(reached)
    )
    return LineRow(
        po_line_id=po_line_id,
        project_id=project_id,
        vendor_id="VND-001",
        po_number=po_number,
        line_number=line_number,
        material_category="WATER_CHILLER",
        description="Water Chiller (Tag 201-14)",
        manufacturer="Ironvane Thermal",
        part_number="IRV-236500-0001",
        quantity=Decimal("6.0"),
        unit_of_measure="EA",
        order_date=ORDER_DATE,
        need_by_date=ORDER_DATE + timedelta(days=120),
        criticality=3,
        lifecycle_state=reached[-1],
        is_closed=delivered,
        closing_event_id=events[-1].event_id if delivered else None,
        roster_hash="sha256:" + "0" * 64,
        events=events,
    )


def cohort(censored: int, delivered: int) -> tuple[LineRow, ...]:
    """`censored + delivered` lines with unique natural keys, in canonical order."""
    rows = [
        make_line(
            f"PRJ-{1 + index % 5:03d}", f"PO-{index:04d}-0001", 1 + index % 3, delivered=False
        )
        for index in range(censored)
    ] + [
        make_line(
            f"PRJ-{1 + index % 5:03d}", f"PO-{5000 + index:04d}-0001", 1 + index % 3, delivered=True
        )
        for index in range(delivered)
    ]
    return tuple(sorted(rows, key=lambda row: row.natural_key))


def sides(result: SplitResult) -> dict[uuid.UUID, str]:
    return {row.po_line_id: row.split_side for row in result.assignments}


def held_out_count(result: SplitResult, *, censored: bool) -> int:
    return sum(
        1
        for row in result.assignments
        if row.is_censored is censored and row.split_side == HELD_OUT
    )


def stratum_size(result: SplitResult, *, censored: bool) -> int:
    return sum(1 for row in result.assignments if row.is_censored is censored)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Both strata swept from empty to a few dozen, with at least one line overall.
cohort_shapes = st.tuples(
    st.integers(min_value=0, max_value=20), st.integers(min_value=0, max_value=30)
).filter(lambda shape: sum(shape) >= 1)

input_hashes = st.integers(min_value=0, max_value=2**32).map(lambda n: input_hash(f"hash-{n}"))


# ---------------------------------------------------------------------------
# Invariant: one side per line, ordinals contiguous from 1 in canonical order
# ---------------------------------------------------------------------------


@given(shape=cohort_shapes, digest=input_hashes)
def test_every_line_lands_on_exactly_one_side(shape: tuple[int, int], digest: str) -> None:
    """DV-006's half that no constraint can carry (G-6).

    The primary key gives *at most once per run*; that every line appears at all
    is a count against the input, and it is asserted here over the assignment
    the job produces rather than over the table it later writes.
    """
    lines = cohort(*shape)
    result = assign_split(lines, as_of_date=AS_OF_DATE, input_data_hash=digest)

    assert len(result.assignments) == len(lines)
    assert {row.po_line_id for row in result.assignments} == {line.po_line_id for line in lines}
    assert all(row.split_side in {TRAIN, HELD_OUT} for row in result.assignments)


@given(shape=cohort_shapes, digest=input_hashes)
def test_the_canonical_ordinal_is_contiguous_from_one(shape: tuple[int, int], digest: str) -> None:
    """`uq_forecast_split_assignment__run_ordinal` forbids a duplicate; a *gap* is free.

    `data-model.md` § Canonical order has the ordinal stand for a position in the
    serialized sequence, so a gap makes the hash's input a sequence with a hole
    in it — well formed by every constraint and not the thing that was hashed.
    """
    result = assign_split(cohort(*shape), as_of_date=AS_OF_DATE, input_data_hash=digest)
    ordinals = [row.canonical_ordinal for row in result.assignments]

    assert ordinals == list(range(1, len(result.assignments) + 1))


@given(shape=cohort_shapes, digest=input_hashes)
def test_the_assignment_is_returned_in_ascending_natural_key_order(
    shape: tuple[int, int], digest: str
) -> None:
    """Ascending `(project_id, po_number, line_number)`, which is total by DV-023.

    The natural key is unique, so no tie-break exists to specify and none may be
    invented. Asserted on the returned sequence as well as on the ordinal,
    because the digest is taken over the sequence.
    """
    lines = cohort(*shape)
    result = assign_split(lines, as_of_date=AS_OF_DATE, input_data_hash=digest)
    keys = [(row.project_id, row.po_number, row.line_number) for row in result.assignments]

    assert keys == sorted(keys)
    assert keys == sorted(line.natural_key for line in lines)


@given(shape=cohort_shapes, digest=input_hashes)
def test_the_stored_stratum_matches_the_line_it_belongs_to(
    shape: tuple[int, int], digest: str
) -> None:
    """FR-004's stored indicator travels with the assignment, not beside it."""
    censored, delivered = shape
    result = assign_split(cohort(*shape), as_of_date=AS_OF_DATE, input_data_hash=digest)

    assert stratum_size(result, censored=True) == censored
    assert stratum_size(result, censored=False) == delivered


def test_the_assignment_records_carry_the_five_serialized_fields() -> None:
    """`data-model.md` § Hashes serializes exactly these, ordered by the ordinal.

    Named as a test rather than left to the digest, because a record missing one
    of them makes `split_assignment_hash` unbuildable and the failure would
    otherwise surface three modules away.
    """
    result = assign_split(cohort(2, 6), as_of_date=AS_OF_DATE, input_data_hash=input_hash("fields"))
    row = result.assignments[0]

    assert isinstance(row, SplitAssignment)
    for field in ("project_id", "po_number", "line_number", "split_side", "is_censored"):
        assert hasattr(row, field), f"{field!r} is inside the split serialization"
    assert isinstance(row.is_censored, bool)


# ---------------------------------------------------------------------------
# Invariant: the realized proportion, in both strata
# ---------------------------------------------------------------------------


@given(shape=cohort_shapes, digest=input_hashes)
def test_each_stratum_realizes_the_declared_fraction_to_within_one_line(
    shape: tuple[int, int], digest: str
) -> None:
    """Both strata, which is the half a single aggregate check would hide.

    An unstratified split hits the aggregate fraction while putting all 24
    censored lines on one side — and FR-006's realized held-out **uncensored
    event count**, the quantity a calibration band's precision depends on, is
    then whatever the shuffle happened to give.
    """
    result = assign_split(cohort(*shape), as_of_date=AS_OF_DATE, input_data_hash=digest)

    for censored in (True, False):
        size = stratum_size(result, censored=censored)
        realized = held_out_count(result, censored=censored)
        assert abs(realized - HELD_OUT_FRACTION * size) <= 1.0, (
            f"the {'censored' if censored else 'delivered'} stratum has {size} lines and "
            f"{realized} held out; the declared fraction is {HELD_OUT_FRACTION}"
        )


def test_the_realized_shape_splits_both_strata_within_one_line() -> None:
    """The domain's named case: 24 censored against 175 delivered."""
    result = assign_split(
        cohort(REALIZED_CENSORED, REALIZED_DELIVERED),
        as_of_date=AS_OF_DATE,
        input_data_hash=input_hash("realized"),
    )

    assert len(result.assignments) == REALIZED_CENSORED + REALIZED_DELIVERED
    assert abs(held_out_count(result, censored=True) - 0.25 * REALIZED_CENSORED) <= 1.0
    assert abs(held_out_count(result, censored=False) - 0.25 * REALIZED_DELIVERED) <= 1.0


@pytest.mark.parametrize(
    "shape",
    [
        (1, 1),  # strata of size 1, both of them
        (1, 175),  # one censored line against the realized delivered count
        (3, 3),  # a stratum smaller than 1/0.25, so the exact quota is fractional
        (0, 20),  # the degenerate case: zero censored lines
        (20, 0),  # and its mirror, which the domain column does not name
    ],
)
def test_the_named_boundary_shapes_split_within_one_line(shape: tuple[int, int]) -> None:
    """Strata of size 1, a stratum below `1/0.25`, and zero censored.

    A stratum of one cannot realize 0.25 of anything; "within one line" is what
    makes the requirement satisfiable there, and an implementation that rounded
    the quota up would hold out the single line and train on nothing.
    """
    result = assign_split(
        cohort(*shape), as_of_date=AS_OF_DATE, input_data_hash=input_hash(f"shape-{shape}")
    )

    for censored in (True, False):
        size = stratum_size(result, censored=censored)
        assert abs(held_out_count(result, censored=censored) - HELD_OUT_FRACTION * size) <= 1.0


def test_a_single_line_cohort_is_still_assigned() -> None:
    """One line, one side, ordinal 1 — no empty result and no refusal."""
    result = assign_split(cohort(0, 1), as_of_date=AS_OF_DATE, input_data_hash=input_hash("single"))

    assert len(result.assignments) == 1
    assert result.assignments[0].canonical_ordinal == 1
    assert result.assignments[0].split_side in {TRAIN, HELD_OUT}


# ---------------------------------------------------------------------------
# Metamorphic: reordering the input rows changes no line's side
# ---------------------------------------------------------------------------


@given(shape=cohort_shapes, digest=input_hashes, data=st.data())
def test_reordering_the_input_rows_changes_no_lines_side(
    shape: tuple[int, int], digest: str, data: st.DataObject
) -> None:
    """The split is a function of the rows, never of the order they arrived in.

    `read.py` sorts, so in production the two orders coincide — which is exactly
    why this has to be asserted rather than observed: an implementation that
    permuted by position would agree with itself on every real run and disagree
    the first time a caller passed an unsorted frame.
    """
    lines = cohort(*shape)
    shuffled = tuple(data.draw(st.permutations(lines)))

    assert sides(assign_split(shuffled, as_of_date=AS_OF_DATE, input_data_hash=digest)) == sides(
        assign_split(lines, as_of_date=AS_OF_DATE, input_data_hash=digest)
    )


@given(shape=cohort_shapes, digest=input_hashes, data=st.data())
def test_reordering_the_input_rows_reproduces_the_same_digest(
    shape: tuple[int, int], digest: str, data: st.DataObject
) -> None:
    """The same statement at the level FR-023 refuses on."""
    lines = cohort(*shape)
    shuffled = tuple(data.draw(st.permutations(lines)))

    assert (
        assign_split(shuffled, as_of_date=AS_OF_DATE, input_data_hash=digest).split_assignment_hash
        == assign_split(lines, as_of_date=AS_OF_DATE, input_data_hash=digest).split_assignment_hash
    )


# ---------------------------------------------------------------------------
# Invariant (AD-011): a pure function of the hash and two committed constants
# ---------------------------------------------------------------------------


def test_neither_the_seed_nor_the_fraction_is_a_per_call_argument() -> None:
    """AD-011 stated mechanically, over the signature rather than in prose.

    A per-run seed or a per-run fraction lets a re-fit reshuffle the split until
    a vendor lands favourably, which is FR-028's prohibition reached by another
    route. Both are committed configuration; a parameter for either would make
    the freedom available whether or not anybody used it.
    """
    parameters = inspect.signature(assign_split).parameters

    assert tuple(parameters) == ("lines", "as_of_date", "input_data_hash")
    assert SPLIT_SEED == 20260727
    assert HELD_OUT_FRACTION == 0.25


@given(shape=cohort_shapes, digest=input_hashes)
def test_a_second_call_with_identical_inputs_reproduces_the_assignment(
    shape: tuple[int, int], digest: str
) -> None:
    """NC-13's passing direction: the same rows at the same anchor, twice."""
    lines = cohort(*shape)
    first = assign_split(lines, as_of_date=AS_OF_DATE, input_data_hash=digest)
    second = assign_split(lines, as_of_date=AS_OF_DATE, input_data_hash=digest)

    assert first.assignments == second.assignments
    assert first.split_assignment_hash == second.split_assignment_hash


def test_a_moved_input_digest_moves_the_split_assignment() -> None:
    """NC-13's other direction: one mutated row keys a different split.

    Asserted at the realized 199-line shape, where an assignment holding ~50
    lines out could coincide across two keys only by an accident with no
    plausible probability — and where a split that ignored the key entirely,
    which is the implementation this exists to exclude, agrees exactly.
    """
    lines = cohort(REALIZED_CENSORED, REALIZED_DELIVERED)
    before = assign_split(lines, as_of_date=AS_OF_DATE, input_data_hash=input_hash("before"))
    after = assign_split(lines, as_of_date=AS_OF_DATE, input_data_hash=input_hash("after"))

    assert before.split_assignment_hash != after.split_assignment_hash
    assert sides(before) != sides(after)


def test_the_digest_is_a_function_of_the_assignment_alone() -> None:
    """Two runs that assign identically hash identically, whatever keyed them.

    The pair above shows a different key moves the split. This shows the digest
    carries nothing *but* the split: rebuild the same cohort from scratch and
    the hash is the same object identity never entered.
    """
    digest = input_hash("stability")
    first = assign_split(
        cohort(REALIZED_CENSORED, REALIZED_DELIVERED), as_of_date=AS_OF_DATE, input_data_hash=digest
    )
    second = assign_split(
        cohort(REALIZED_CENSORED, REALIZED_DELIVERED), as_of_date=AS_OF_DATE, input_data_hash=digest
    )

    assert first.split_assignment_hash == second.split_assignment_hash


@given(shape=cohort_shapes, digest=input_hashes)
def test_the_as_of_date_reaches_the_split_only_through_the_stratum(
    shape: tuple[int, int], digest: str
) -> None:
    """Two as-of dates that censor the same lines assign the same sides.

    AD-011 names three determinants and the as-of date is not among them: it
    enters only by deciding which stratum each line is in. Both dates below sit
    after every terminal event in the cohort, so the strata coincide and so must
    the assignment.
    """
    lines = cohort(*shape)
    first = assign_split(lines, as_of_date=AS_OF_DATE, input_data_hash=digest)
    later = assign_split(lines, as_of_date=AS_OF_DATE + timedelta(days=90), input_data_hash=digest)

    assert sides(first) == sides(later)


def test_the_assignment_does_not_carry_a_line_the_input_did_not() -> None:
    """`fk_forecast_split_assignment__line` would reject it; nothing should offer it."""
    lines = cohort(4, 12)
    result = assign_split(lines, as_of_date=AS_OF_DATE, input_data_hash=input_hash("closure"))
    known = {line.po_line_id for line in lines}

    assert all(row.po_line_id in known for row in result.assignments)


def test_the_input_rows_are_not_mutated_by_the_split() -> None:
    """A pure function leaves its argument alone; `LineRow` is frozen, so prove it."""
    lines = cohort(3, 9)
    before = tuple(replace(line) for line in lines)
    assign_split(lines, as_of_date=AS_OF_DATE, input_data_hash=input_hash("purity"))

    assert lines == before
