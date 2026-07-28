"""T017 (RED) — `forecast/serialize.py`'s two digests at the property tier.

`plan.md` § Mandated properties gives this module one relation, **metamorphic**:
the digest is invariant to row order and to `created_at`, and moves on any value
inside the serialization. Its stated input domain is a reload of identical
content, a single mutated cell, and a NULL against an empty string. The module
under test does not exist yet — this file is the RED half of T017/T018 and must
fail at collection. `input_data_hash` takes the `ProcurementInput` `read.py`
already returns; `split_assignment_hash` takes the assignment records in any
order and imposes `canonical_ordinal` itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from model.forecast.serialize import input_data_hash, split_assignment_hash

from model.corpus.manifest import DIGEST_PATTERN
from model.forecast.read import (
    HASHED_EVENT_FIELDS,
    HASHED_LINE_FIELDS,
    LifecycleEventRow,
    LineRow,
    ProcurementInput,
)

#: Namespace for the deterministic surrogate keys these fixtures carry. `uuid5`
#: rather than `uuid4` so a rebuilt row keeps its identity, which is what makes
#: "a reload of identical content" expressible at all (E005 AD-003).
NS = uuid.uuid5(uuid.NAMESPACE_URL, "e007/tests/forecast/serialize-properties")

#: Every `occurred_at` is offset from here and carries UTC, because `read.py`
#: refuses a naive instant and normalizes an aware one.
EPOCH = datetime(2025, 9, 1, tzinfo=UTC)

#: `created_at` is the column the metamorphic property names by name. It is not
#: a field of `LineRow` at all — E005's compared-content projections exclude it —
#: so the invariance is structural, and the assertion below says so rather than
#: leaving a reader to infer it from an absence.
LOAD_TIME_COLUMN = "created_at"


def line_uuid(project_id: str, po_number: str, line_number: int) -> uuid.UUID:
    return uuid.uuid5(NS, f"pol|{project_id}|{po_number}|{line_number}")


def make_event(
    po_line_id: uuid.UUID,
    sequence_no: int,
    to_state: str,
    *,
    day: int,
    is_terminal: bool = False,
    from_state: str | None = None,
    note: str | None = None,
) -> LifecycleEventRow:
    return LifecycleEventRow(
        event_id=uuid.uuid5(NS, f"evt|{po_line_id}|{sequence_no}"),
        po_line_id=po_line_id,
        sequence_no=sequence_no,
        from_state=from_state,
        to_state=to_state,
        is_terminal=is_terminal,
        occurred_at=EPOCH + timedelta(days=day),
        note=note,
    )


def make_line(
    project_id: str = "PRJ-001",
    po_number: str = "PO-001-0001",
    line_number: int = 1,
    **overrides: Any,
) -> LineRow:
    """One line carrying every hashed field, with the natural key up front."""
    base = LineRow(
        po_line_id=line_uuid(project_id, po_number, line_number),
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
        order_date=date(2025, 9, 1),
        need_by_date=date(2026, 1, 5),
        criticality=3,
        lifecycle_state="submitted",
        is_closed=False,
        closing_event_id=None,
        roster_hash="sha256:" + "0" * 64,
        events=(),
    )
    return replace(base, **overrides)


@dataclass(frozen=True, slots=True)
class SplitRow:
    """An assignment record, in the shape `data-model.md` § Hashes serializes.

    The five hashed fields plus the ordinal that orders them. Declared here and
    not imported from `split.py`, deliberately: T018 must be able to turn this
    file green on its own, and a test that could only pass once a later pair had
    landed would silently move the red-green boundary it exists to enforce.
    """

    project_id: str
    po_number: str
    line_number: int
    split_side: str
    is_censored: bool
    canonical_ordinal: int


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

PROJECTS = ("PRJ-001", "PRJ-002", "PRJ-003")
PO_NUMBERS = ("PO-001-0001", "PO-001-0002", "PO-002-0001")
STATES = ("submitted", "under_review", "approved", "shipped", "delivered")

natural_keys = st.tuples(
    st.sampled_from(PROJECTS), st.sampled_from(PO_NUMBERS), st.integers(min_value=1, max_value=4)
)


@st.composite
def procurement_inputs(draw: st.DrawFn) -> ProcurementInput:
    """A whole fit input in canonical order: lines by natural key, events by sequence."""
    keys = draw(st.lists(natural_keys, min_size=1, max_size=4, unique=True))
    lines = []
    for project_id, po_number, line_number in sorted(keys):
        po_line_id = line_uuid(project_id, po_number, line_number)
        events = tuple(
            make_event(
                po_line_id,
                sequence_no,
                draw(st.sampled_from(STATES)),
                day=sequence_no * 3,
                note=draw(st.none() | st.text(max_size=6)),
            )
            for sequence_no in range(1, draw(st.integers(min_value=0, max_value=3)) + 1)
        )
        lines.append(
            make_line(
                project_id,
                po_number,
                line_number,
                vendor_id=draw(st.sampled_from(("VND-001", "VND-002"))),
                criticality=draw(st.integers(min_value=1, max_value=5)),
                events=events,
            )
        )
    return ProcurementInput(lines=tuple(lines))


def shuffled(source: ProcurementInput, data: st.DataObject) -> ProcurementInput:
    """The same content, read in a different order — lines and events both."""
    lines = [
        replace(line, events=tuple(data.draw(st.permutations(line.events))))
        for line in source.lines
    ]
    return ProcurementInput(lines=tuple(data.draw(st.permutations(lines))))


# ---------------------------------------------------------------------------
# Metamorphic: row order
# ---------------------------------------------------------------------------


@given(source=procurement_inputs(), data=st.data())
def test_the_input_digest_ignores_the_order_the_rows_arrived_in(
    source: ProcurementInput, data: st.DataObject
) -> None:
    """The canonical order is imposed by the serializer, not assumed of the caller.

    `data-model.md` § Hashes defines the input row hash over lines ascending by
    `(project_id, po_number, line_number)` and events by `sequence_no`. A digest
    that merely inherited whatever order it was handed would be a function of the
    query plan, and FR-023's refusal would fire on a re-read that changed nothing.
    """
    assert input_data_hash(shuffled(source, data)) == input_data_hash(source)


@given(source=procurement_inputs())
def test_a_reload_of_identical_content_reproduces_the_digest(source: ProcurementInput) -> None:
    """DV-015's first clause: `created_at` moves on every load and the hash does not."""
    reloaded = ProcurementInput(
        lines=tuple(replace(line, events=tuple(line.events)) for line in source.lines)
    )
    assert reloaded is not source
    assert input_data_hash(reloaded) == input_data_hash(source)


def test_the_load_time_column_is_outside_the_hashed_field_sets() -> None:
    """What makes the invariance above non-vacuous rather than a coincidence.

    Two identical reloads agree trivially if nothing distinguishes them. The
    reason they *must* agree is that the load-time column is not a member of
    either compared-content projection, so it is never read and cannot enter the
    serialization. Asserted against the projections `read.py` imports from the
    loader, so a future column added to one of them is caught here.
    """
    assert LOAD_TIME_COLUMN not in HASHED_LINE_FIELDS
    assert LOAD_TIME_COLUMN not in HASHED_EVENT_FIELDS
    assert len(HASHED_LINE_FIELDS) == 17
    assert len(HASHED_EVENT_FIELDS) == 6


# ---------------------------------------------------------------------------
# Metamorphic: a single mutated cell
# ---------------------------------------------------------------------------

#: One replacement value per hashed line field, each visibly different from the
#: value `make_line` carries. Keyed by field name so the parametrization below
#: quantifies over `HASHED_LINE_FIELDS` itself: "moves on any value inside the
#: serialization" is a claim about the whole projection, and a hand-picked
#: subset would leave a field uncovered the day it stopped being serialized.
LINE_MUTATIONS: dict[str, Any] = {
    "project_id": "PRJ-009",
    "vendor_id": "VND-002",
    "po_number": "PO-009-0009",
    "line_number": 9,
    "material_category": "SWITCHGEAR",
    "description": "Water Chiller (Tag 201-15)",
    "manufacturer": "Dornthorne Fabrication",
    "part_number": "IRV-236500-0002",
    "quantity": Decimal("6.5"),
    "unit_of_measure": "LOT",
    "order_date": date(2025, 9, 2),
    "need_by_date": date(2026, 1, 6),
    "criticality": 4,
    "lifecycle_state": "under_review",
    "is_closed": True,
    "closing_event_id": uuid.uuid5(NS, "closing-event"),
    "roster_hash": "sha256:" + "1" * 64,
}

#: The same, for the six compared event fields.
EVENT_MUTATIONS: dict[str, Any] = {
    "sequence_no": 2,
    "from_state": "submitted",
    "to_state": "approved",
    "is_terminal": True,
    "occurred_at": EPOCH + timedelta(days=99),
    "note": "amended",
}


@pytest.mark.parametrize("field", HASHED_LINE_FIELDS)
def test_mutating_any_hashed_line_field_moves_the_digest(field: str) -> None:
    assert field in LINE_MUTATIONS, (
        f"{field!r} is inside the input serialization and this file has no mutation for "
        f"it, so 'the digest moves on any value' is asserted over 16 of 17 fields. Add a "
        f"replacement value to LINE_MUTATIONS."
    )
    original = ProcurementInput(lines=(make_line(),))
    mutated = ProcurementInput(
        lines=(replace(original.lines[0], **{field: LINE_MUTATIONS[field]}),)
    )

    assert input_data_hash(mutated) != input_data_hash(original), (
        f"{field!r} changed and the input row hash did not. FR-023 refuses on this digest, "
        f"so a field outside it is a cell that can be edited in the database and reproduce "
        f"cleanly (DV-015)."
    )


@pytest.mark.parametrize("field", HASHED_EVENT_FIELDS)
def test_mutating_any_hashed_event_field_moves_the_digest(field: str) -> None:
    assert field in EVENT_MUTATIONS, (
        f"{field!r} is inside the input serialization and this file has no mutation for it."
    )
    po_line_id = line_uuid("PRJ-001", "PO-001-0001", 1)
    event = make_event(po_line_id, 1, "submitted", day=0)
    original = ProcurementInput(lines=(make_line(events=(event,)),))
    mutated = ProcurementInput(
        lines=(make_line(events=(replace(event, **{field: EVENT_MUTATIONS[field]}),)),)
    )

    assert input_data_hash(mutated) != input_data_hash(original)


# ---------------------------------------------------------------------------
# Boundary: a NULL against an empty string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["from_state", "note"])
def test_a_null_and_an_empty_string_are_different_content(field: str) -> None:
    """The domain's third case, and the one a stringifying serializer gets wrong.

    `str(None)` and `""` both render as something; only a serializer that keeps
    JSON `null` distinct from `""` gives two digests here. DV-022 requires `note`
    NULL on every E005 event, so a serializer that could not tell the two apart
    would let a blanked-out note reproduce as if nothing had changed.
    """
    po_line_id = line_uuid("PRJ-001", "PO-001-0001", 1)
    absent = make_event(po_line_id, 1, "submitted", day=0, **{field: None})
    empty = make_event(po_line_id, 1, "submitted", day=0, **{field: ""})

    assert input_data_hash(
        ProcurementInput(lines=(make_line(events=(absent,)),))
    ) != input_data_hash(ProcurementInput(lines=(make_line(events=(empty,)),)))


def test_a_null_closing_event_is_not_the_same_as_a_present_one() -> None:
    """The same boundary on the line side, where the nullable column is a uuid."""
    absent = ProcurementInput(lines=(make_line(),))
    present = ProcurementInput(lines=(make_line(closing_event_id=uuid.uuid5(NS, "closing")),))

    assert input_data_hash(absent) != input_data_hash(present)


# ---------------------------------------------------------------------------
# The published surface form
# ---------------------------------------------------------------------------


@given(source=procurement_inputs())
def test_the_input_digest_has_the_form_the_check_constraint_admits(
    source: ProcurementInput,
) -> None:
    """`ck_forecast_run__input_hash_format` is `^sha256:[0-9a-f]{64}$`."""
    assert DIGEST_PATTERN.fullmatch(input_data_hash(source))


# ---------------------------------------------------------------------------
# The split assignment digest
# ---------------------------------------------------------------------------


@st.composite
def split_rows(draw: st.DrawFn) -> tuple[SplitRow, ...]:
    """Assignment records in canonical order, one per line, ordinals from 1."""
    keys = sorted(draw(st.lists(natural_keys, min_size=1, max_size=6, unique=True)))
    return tuple(
        SplitRow(
            project_id=project_id,
            po_number=po_number,
            line_number=line_number,
            split_side=draw(st.sampled_from(("train", "held_out"))),
            is_censored=draw(st.booleans()),
            canonical_ordinal=ordinal,
        )
        for ordinal, (project_id, po_number, line_number) in enumerate(keys, start=1)
    )


@given(rows=split_rows(), data=st.data())
def test_the_split_digest_ignores_the_order_the_records_arrived_in(
    rows: tuple[SplitRow, ...], data: st.DataObject
) -> None:
    """Ordered by `canonical_ordinal`, which the serializer imposes rather than trusts."""
    assert split_assignment_hash(data.draw(st.permutations(rows))) == split_assignment_hash(rows)


@given(rows=split_rows())
def test_the_split_digest_has_the_form_the_check_constraint_admits(
    rows: tuple[SplitRow, ...],
) -> None:
    """`ck_forecast_run__split_hash_format`, the same surface as the input hash."""
    assert DIGEST_PATTERN.fullmatch(split_assignment_hash(rows))


@given(rows=split_rows(), data=st.data())
def test_flipping_one_line_side_moves_the_split_digest(
    rows: tuple[SplitRow, ...], data: st.DataObject
) -> None:
    """NC-3's second case rests on this: a mutated split assignment must be visible."""
    position = data.draw(st.integers(min_value=0, max_value=len(rows) - 1))
    flipped = "held_out" if rows[position].split_side == "train" else "train"
    mutated = (*rows[:position], replace(rows[position], split_side=flipped), *rows[position + 1 :])

    assert split_assignment_hash(mutated) != split_assignment_hash(rows)


@given(rows=split_rows(), data=st.data())
def test_flipping_one_censoring_indicator_moves_the_split_digest(
    rows: tuple[SplitRow, ...], data: st.DataObject
) -> None:
    """`is_censored` is inside the serialization: the stratum is part of the claim."""
    position = data.draw(st.integers(min_value=0, max_value=len(rows) - 1))
    row = rows[position]
    mutated = (
        *rows[:position],
        replace(row, is_censored=not row.is_censored),
        *rows[position + 1 :],
    )

    assert split_assignment_hash(mutated) != split_assignment_hash(rows)
