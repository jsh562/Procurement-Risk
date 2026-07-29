"""T114 (RED) — the vendor and category index mapping, at the property tier.

`design.py` is the tenth mandatory property module (A-002): a mis-indexed
mapping is shape-preserving, so the graph builds, samples and returns a
posterior that is plausible in every respect except which vendor each effect
belongs to. Two relations are asserted. **Metamorphic**: permuting two vendors
permutes exactly their two columns, so a swap is detected. **Invariant**: the
mapping is a pure function of the roster order, and a vendor appended at the end
moves no existing column. Domain: the realized 12 vendors and 20 material
categories, a single-vendor roster, and a category with no lines.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from model.forecast.design import category_index, design_matrix, vendor_index
from model.forecast.read import LineRow
from model.procurement.durations import TIER_OFFSETS
from model.roster.reader import read_roster

NS = uuid.uuid5(uuid.NAMESPACE_URL, "e007/tests/forecast/design-properties")

#: The roster as committed, in the order the file lists it. Read rather than
#: retyped: "12 vendors as realized" is a fact about `data/roster`, and a
#: hand-copied list here would keep passing after the roster moved.
REALIZED_VENDORS: tuple[str, ...] = tuple(entry.id for entry in read_roster().vendors)

#: The 20 committed material-category keys, from the module that owns them.
REALIZED_CATEGORIES: tuple[str, ...] = tuple(sorted(TIER_OFFSETS))

ORDER_DATE = date(2025, 9, 1)


def make_line(vendor_id: str, material_category: str, ordinal: int) -> LineRow:
    """One training line carrying the two fields the design matrix indexes on."""
    po_line_id = uuid.uuid5(NS, f"pol|{ordinal}")
    return LineRow(
        po_line_id=po_line_id,
        project_id=f"PRJ-{1 + ordinal % 5:03d}",
        vendor_id=vendor_id,
        po_number=f"PO-{ordinal:04d}-0001",
        line_number=1,
        material_category=material_category,
        description="Water Chiller (Tag 201-14)",
        manufacturer="Ironvane Thermal",
        part_number="IRV-236500-0001",
        quantity=Decimal("6.0"),
        unit_of_measure="EA",
        order_date=ORDER_DATE,
        need_by_date=ORDER_DATE + timedelta(days=120),
        criticality=3,
        lifecycle_state="shipped",
        is_closed=False,
        closing_event_id=None,
        roster_hash="sha256:" + "0" * 64,
        events=(),
    )


def swap(values: tuple[str, ...], first: int, second: int) -> tuple[str, ...]:
    """`values` with two positions exchanged — the planted mis-index."""
    mutated = list(values)
    mutated[first], mutated[second] = mutated[second], mutated[first]
    return tuple(mutated)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

vendor_rosters = st.lists(
    st.sampled_from(REALIZED_VENDORS), min_size=1, max_size=len(REALIZED_VENDORS), unique=True
).map(tuple)

category_sets = st.lists(
    st.sampled_from(REALIZED_CATEGORIES), min_size=1, max_size=8, unique=True
).map(tuple)


@st.composite
def designs(draw: st.DrawFn) -> tuple[tuple[LineRow, ...], tuple[str, ...], tuple[str, ...]]:
    """A roster, a category set, and lines drawn from both — never beyond them."""
    vendors = draw(vendor_rosters)
    categories = draw(category_sets)
    memberships = draw(
        st.lists(
            st.tuples(st.sampled_from(vendors), st.sampled_from(categories)),
            min_size=1,
            max_size=20,
        )
    )
    lines = tuple(
        make_line(vendor_id, category, ordinal)
        for ordinal, (vendor_id, category) in enumerate(memberships)
    )
    return lines, vendors, categories


# ---------------------------------------------------------------------------
# Invariant: the mapping is a bijection from roster order to column position
# ---------------------------------------------------------------------------


@given(vendors=vendor_rosters)
def test_the_vendor_mapping_is_a_bijection_onto_the_column_positions(
    vendors: tuple[str, ...],
) -> None:
    """Every vendor has a column, every column has a vendor, and none is shared.

    The three failures this excludes are different: a missing vendor drops a
    hierarchy member silently, a duplicated position sums two vendors' lines
    into one offset, and a gap leaves a column no line ever loads — all of which
    sample cleanly.
    """
    mapping = vendor_index(vendors)

    assert set(mapping) == set(vendors)
    assert sorted(mapping.values()) == list(range(len(vendors)))


@given(vendors=vendor_rosters)
def test_the_vendor_mapping_follows_the_roster_order(vendors: tuple[str, ...]) -> None:
    """Position `i` in the roster is column `i`. The mapping *is* the roster order."""
    mapping = vendor_index(vendors)

    assert all(mapping[vendor_id] == position for position, vendor_id in enumerate(vendors))


@given(categories=category_sets)
def test_the_category_mapping_is_a_bijection_following_its_own_order(
    categories: tuple[str, ...],
) -> None:
    """The same claim for the second hierarchy, which pools over material category."""
    mapping = category_index(categories)

    assert set(mapping) == set(categories)
    assert sorted(mapping.values()) == list(range(len(categories)))
    assert all(mapping[category] == position for position, category in enumerate(categories))


@pytest.mark.parametrize("build", [vendor_index, category_index])
def test_a_duplicated_identifier_is_refused(build: object) -> None:
    """A bijection cannot be built from a roster that names one member twice.

    Silently de-duplicating would shorten the parameter vector by one and shift
    every column after the duplicate — the exact mis-index this module exists to
    make impossible, arrived at by way of a tolerant input reader.
    """
    with pytest.raises(ValueError):
        build(("VND-001", "VND-002", "VND-001"))  # type: ignore[operator]


@pytest.mark.parametrize("build", [vendor_index, category_index])
def test_an_empty_roster_is_refused(build: object) -> None:
    """An empty index yields a matrix with no columns and a hierarchy over nothing."""
    with pytest.raises(ValueError):
        build(())  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Invariant: a pure function of the roster order
# ---------------------------------------------------------------------------


@given(vendors=vendor_rosters, categories=category_sets)
def test_the_same_roster_yields_the_same_mapping(
    vendors: tuple[str, ...], categories: tuple[str, ...]
) -> None:
    """Called twice, in one process, with nothing carried between the calls.

    A mapping built from a `set`, a `dict` iterated before insertion order was
    fixed, or anything keyed on object identity would be stable within a call
    and unstable across runs — and the run that wrote the posterior is not the
    run that reads it.
    """
    assert dict(vendor_index(vendors)) == dict(vendor_index(vendors))
    assert dict(category_index(categories)) == dict(category_index(categories))


@given(built=designs())
def test_the_mapping_does_not_depend_on_which_lines_were_read(
    built: tuple[tuple[LineRow, ...], tuple[str, ...], tuple[str, ...]],
) -> None:
    """Column position comes from the roster, never from the data.

    If the index were derived from the lines, a vendor with no training line
    would lose its column — and FR-011 requires a shrinkage weight for *every*
    vendor, including the one that never appears in the training split.
    """
    lines, vendors, categories = built
    matrix = design_matrix(lines, vendor_ids=vendors, material_categories=categories)

    assert matrix.shape == (len(lines), len(vendors) + len(categories))


@given(vendors=vendor_rosters, categories=category_sets)
def test_a_vendor_appended_at_the_end_moves_no_existing_column(
    vendors: tuple[str, ...], categories: tuple[str, ...]
) -> None:
    """Growth is at the tail, so a thirteenth vendor cannot renumber the first twelve.

    Stated over both the mapping and the matrix. A design that sorted the roster
    on its way in would satisfy the bijection above and still move a column here
    the first time a vendor identifier landed out of alphabetical order.
    """
    added = "VND-999"
    lines = tuple(
        make_line(vendor_id, categories[index % len(categories)], index)
        for index, vendor_id in enumerate(vendors)
    )

    before = vendor_index(vendors)
    after = vendor_index((*vendors, added))

    assert all(after[vendor_id] == before[vendor_id] for vendor_id in vendors)
    assert after[added] == len(vendors)

    narrow = design_matrix(lines, vendor_ids=vendors, material_categories=categories)
    wide = design_matrix(lines, vendor_ids=(*vendors, added), material_categories=categories)

    assert np.array_equal(wide[:, : len(vendors)], narrow[:, : len(vendors)])
    assert not wide[:, len(vendors)].any(), "no line names the appended vendor"
    assert np.array_equal(wide[:, len(vendors) + 1 :], narrow[:, len(vendors) :])


# ---------------------------------------------------------------------------
# Metamorphic: a swapped vendor index must be detected
# ---------------------------------------------------------------------------


@given(built=designs(), data=st.data())
def test_permuting_two_vendors_permutes_exactly_their_two_columns(
    built: tuple[tuple[LineRow, ...], tuple[str, ...], tuple[str, ...]],
    data: st.DataObject,
) -> None:
    """The relation a swap has to satisfy, stated as an equality rather than a bound.

    "The matrix changed" would also be true of an implementation that changed it
    the wrong way. What must hold is that swapping vendors `i` and `j` in the
    roster exchanges columns `i` and `j` and touches nothing else — which is
    what makes the vendor offset that comes back attributable to a vendor.
    """
    lines, vendors, categories = built
    if len(vendors) < 2:
        return
    first = data.draw(st.integers(min_value=0, max_value=len(vendors) - 1))
    second = data.draw(st.integers(min_value=0, max_value=len(vendors) - 1))

    original = design_matrix(lines, vendor_ids=vendors, material_categories=categories)
    swapped = design_matrix(
        lines, vendor_ids=swap(vendors, first, second), material_categories=categories
    )

    expected = original.copy()
    expected[:, [first, second]] = original[:, [second, first]]
    assert np.array_equal(swapped, expected)


@given(built=designs(), data=st.data())
def test_a_swap_that_changes_the_membership_changes_the_matrix(
    built: tuple[tuple[LineRow, ...], tuple[str, ...], tuple[str, ...]],
    data: st.DataObject,
) -> None:
    """The detection claim itself: where the two columns differ, the matrix moves.

    Without this the permutation relation above is satisfiable by an
    implementation that ignores the roster entirely — two identical matrices
    trivially exchange two identical columns.
    """
    lines, vendors, categories = built
    if len(vendors) < 2:
        return
    first = data.draw(st.integers(min_value=0, max_value=len(vendors) - 1))
    second = data.draw(st.integers(min_value=0, max_value=len(vendors) - 1))

    original = design_matrix(lines, vendor_ids=vendors, material_categories=categories)
    if np.array_equal(original[:, first], original[:, second]):
        return

    swapped = design_matrix(
        lines, vendor_ids=swap(vendors, first, second), material_categories=categories
    )
    assert not np.array_equal(swapped, original)


@given(built=designs(), data=st.data())
def test_permuting_two_categories_permutes_exactly_their_two_columns(
    built: tuple[tuple[LineRow, ...], tuple[str, ...], tuple[str, ...]],
    data: st.DataObject,
) -> None:
    """The second hierarchy is indexed by the same rule and fails the same way."""
    lines, vendors, categories = built
    if len(categories) < 2:
        return
    first = data.draw(st.integers(min_value=0, max_value=len(categories) - 1))
    second = data.draw(st.integers(min_value=0, max_value=len(categories) - 1))
    offset = len(vendors)

    original = design_matrix(lines, vendor_ids=vendors, material_categories=categories)
    swapped = design_matrix(
        lines, vendor_ids=vendors, material_categories=swap(categories, first, second)
    )

    expected = original.copy()
    expected[:, [offset + first, offset + second]] = original[:, [offset + second, offset + first]]
    assert np.array_equal(swapped, expected)


# ---------------------------------------------------------------------------
# The matrix each row loads
# ---------------------------------------------------------------------------


@given(built=designs())
def test_each_row_loads_its_own_vendor_and_its_own_category(
    built: tuple[tuple[LineRow, ...], tuple[str, ...], tuple[str, ...]],
) -> None:
    """The mapping and the matrix agree, which is what ties the two exports together.

    Asserted per row against the index rather than against a position computed
    here: a test that recomputed the column would be checking its own arithmetic,
    and the failure mode under test is precisely a column computed twice and
    differently.
    """
    lines, vendors, categories = built
    vendors_at = vendor_index(vendors)
    categories_at = category_index(categories)
    matrix = design_matrix(lines, vendor_ids=vendors, material_categories=categories)
    offset = len(vendors)

    for row, line in enumerate(lines):
        vendor_block = matrix[row, :offset]
        category_block = matrix[row, offset:]

        assert vendor_block.sum() == 1.0
        assert category_block.sum() == 1.0
        assert vendor_block[vendors_at[line.vendor_id]] == 1.0
        assert category_block[categories_at[line.material_category]] == 1.0


@given(built=designs())
def test_a_line_naming_an_unknown_vendor_is_refused(
    built: tuple[tuple[LineRow, ...], tuple[str, ...], tuple[str, ...]],
) -> None:
    """Dropping it, or folding it into a catch-all column, is the silent version.

    A line whose vendor is outside the roster cannot be placed. Refusing names
    the line; a zero row would train the population mean on it and report
    nothing, which is a mis-attribution rather than a missing one.
    """
    lines, vendors, categories = built
    stranger = (*lines, make_line("VND-999", categories[0], len(lines)))

    with pytest.raises(ValueError):
        design_matrix(stranger, vendor_ids=vendors, material_categories=categories)


@given(built=designs())
def test_a_line_naming_an_unknown_category_is_refused(
    built: tuple[tuple[LineRow, ...], tuple[str, ...], tuple[str, ...]],
) -> None:
    lines, vendors, categories = built
    stranger = (*lines, make_line(vendors[0], "NOT_A_CATEGORY", len(lines)))

    with pytest.raises(ValueError):
        design_matrix(stranger, vendor_ids=vendors, material_categories=categories)


# ---------------------------------------------------------------------------
# The named domain: 12 vendors, 20 categories, one vendor, an unused category
# ---------------------------------------------------------------------------


def test_the_realized_roster_is_twelve_vendors_and_twenty_categories() -> None:
    """The domain column's first case, asserted before it is relied on below."""
    assert len(REALIZED_VENDORS) == 12
    assert len(REALIZED_CATEGORIES) == 20


def test_the_realized_index_covers_every_vendor_and_every_category() -> None:
    """32 columns, one per hierarchy member, at the shape the fit actually runs."""
    lines = tuple(
        make_line(REALIZED_VENDORS[index % 12], REALIZED_CATEGORIES[index % 19], index)
        for index in range(199)
    )
    matrix = design_matrix(
        lines, vendor_ids=REALIZED_VENDORS, material_categories=REALIZED_CATEGORIES
    )

    assert matrix.shape == (199, 32)
    assert dict(vendor_index(REALIZED_VENDORS))[REALIZED_VENDORS[11]] == 11
    assert dict(category_index(REALIZED_CATEGORIES))[REALIZED_CATEGORIES[19]] == 19


def test_a_category_with_no_lines_keeps_its_column() -> None:
    """The domain's third case: an all-zero column is present, not absent.

    `REALIZED_CATEGORIES[19]` is loaded by no line above. Its column has to
    survive anyway — DV-009 requires a shrinkage entry for every member, and a
    column that disappears when the data are thin is a parameter vector whose
    length depends on the split.
    """
    lines = tuple(
        make_line(REALIZED_VENDORS[index % 12], REALIZED_CATEGORIES[index % 19], index)
        for index in range(199)
    )
    matrix = design_matrix(
        lines, vendor_ids=REALIZED_VENDORS, material_categories=REALIZED_CATEGORIES
    )
    unused = len(REALIZED_VENDORS) + 19

    assert matrix.shape[1] == 32
    assert not matrix[:, unused].any()
    assert matrix[:, len(REALIZED_VENDORS) : unused].sum() == 199


def test_a_single_vendor_roster_gives_one_column_of_ones() -> None:
    """The degenerate roster, where the hierarchy has one member and still has one."""
    vendors = (REALIZED_VENDORS[0],)
    categories = REALIZED_CATEGORIES[:2]
    lines = tuple(make_line(vendors[0], categories[index % 2], index) for index in range(6))

    matrix = design_matrix(lines, vendor_ids=vendors, material_categories=categories)

    assert dict(vendor_index(vendors)) == {vendors[0]: 0}
    assert matrix.shape == (6, 3)
    assert matrix[:, 0].tolist() == [1.0] * 6
